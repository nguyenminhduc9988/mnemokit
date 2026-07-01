"""Persistent memory store backed by SQLite + FTS5 full-text search.

Designed for AI agents, CLI tools, and any app that needs durable, searchable
memory without a database server or external dependencies.

Ranking blends FTS5 BM25 relevance with a configurable recency boost, so recent
or frequently-accessed memories surface higher.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}',
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    accessed_at REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    ttl         REAL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text,
    tags,
    content='memories',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, tags)
    VALUES (new.id, new.text, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags)
    VALUES ('delete', old.id, old.text, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags)
    VALUES ('delete', old.id, old.text, old.tags);
    INSERT INTO memories_fts(rowid, text, tags)
    VALUES (new.id, new.text, new.tags);
END;
"""


@dataclass
class Memory:
    """A single stored memory with relevance score attached on search."""

    id: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    accessed_at: float = 0.0
    access_count: int = 0
    ttl: float | None = None
    score: float = 0.0

    @property
    def expired(self) -> bool:
        """Whether this memory has passed its time-to-live."""
        return self.ttl is not None and self.ttl <= time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "accessed_at": self.accessed_at,
            "access_count": self.access_count,
            "ttl": self.ttl,
        }


@dataclass
class Stats:
    """Aggregate statistics about the store."""

    count: int
    db_path: str
    db_size_bytes: int


class MemoryStore:
    """A durable, searchable memory store.

    Args:
        path: SQLite database file path. Use ``":memory:"`` for an ephemeral
            in-memory store (lost when the process exits).
        recency_weight: 0..1 weight for the recency/access boost mixed into the
            final search score. ``0`` = pure BM25 relevance, ``1`` = pure
            recency. Default ``0.15``.
    """

    def __init__(self, path: str | Path = ":memory:", *, recency_weight: float = 0.15):
        if not 0.0 <= recency_weight <= 1.0:
            raise ValueError("recency_weight must be between 0.0 and 1.0")
        self._path = str(path)
        self._recency_weight = recency_weight
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ core

    def add(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        tags: Iterable[str] | None = None,
        ttl: float | None = None,
    ) -> int:
        """Insert a memory and return its new id.

        Args:
            text: The memory content. Required.
            metadata: Optional arbitrary JSON-serializable metadata.
            tags: Optional list of tags; also indexed for search.
            ttl: Optional absolute expiry timestamp (epoch seconds).
        """
        if not text or not text.strip():
            raise ValueError("text must be a non-empty string")
        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO memories
               (text, metadata, tags, created_at, updated_at, accessed_at, ttl)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                text,
                json.dumps(metadata or {}, ensure_ascii=False),
                json.dumps([t for t in (tags or [])], ensure_ascii=False),
                now,
                now,
                now,
                ttl,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get(self, memory_id: int) -> Memory | None:
        """Return a memory by id, or ``None`` if it doesn't exist."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def update(
        self,
        memory_id: int,
        *,
        text: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: Iterable[str] | None = None,
    ) -> bool:
        """Update fields of a memory. Returns ``True`` if a row was changed."""
        existing = self.get(memory_id)
        if existing is None:
            return False
        self._conn.execute(
            """UPDATE memories
               SET text = ?, metadata = ?, tags = ?, updated_at = ?
               WHERE id = ?""",
            (
                text if text is not None else existing.text,
                json.dumps(metadata if metadata is not None else existing.metadata, ensure_ascii=False),
                json.dumps(list(tags) if tags is not None else existing.tags, ensure_ascii=False),
                time.time(),
                memory_id,
            ),
        )
        self._conn.commit()
        return True

    def delete(self, memory_id: int) -> bool:
        """Delete a memory by id. Returns ``True`` if a row was removed."""
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def clear(self) -> int:
        """Delete every memory. Returns the number of rows removed."""
        count = self.count()
        self._conn.execute("DELETE FROM memories")
        # Rebuild the external-content FTS table after a bulk delete.
        self._conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        self._conn.commit()
        return count

    # ----------------------------------------------------------------- search

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: Iterable[str] | None = None,
        where: str | None = None,
    ) -> list[Memory]:
        """Full-text search over all memories.

        Results are ranked by a blend of BM25 relevance and a recency/access
        boost. Expired memories (past ``ttl``) are filtered out.

        Args:
            query: FTS5 query string. Plain words work; ``*`` enables prefix,
                   ``AND``/``OR``/``NOT`` and ``"phrases"`` are supported.
            limit: Maximum number of results.
            tags: If given, only return memories whose tags include ALL of these.
            where: Optional raw SQL ``WHERE`` clause over the ``memories`` table
                   (e.g. ``"access_count > 2"``). Use cautiously.
        """
        if not query or not query.strip():
            return []
        fts_query = self._escape_query(query)
        where_conditions = ["memories_fts MATCH ?"]
        params: list[Any] = [fts_query]
        if where:
            where_conditions.append(f"({where})")
        if tags:
            tag_list = list(tags)
            for t in tag_list:
                where_conditions.append("m.tags LIKE ?")
                params.append(f'%"{t}"%')
        where_sql = " AND ".join(where_conditions)

        rows = self._conn.execute(
            f"""SELECT m.*, bm25(memories_fts) AS rank,
                       m.accessed_at, m.created_at, m.access_count
                FROM memories_fts AS f
                JOIN memories AS m ON m.id = f.rowid
                WHERE {where_sql}
                ORDER BY rank ASC
                LIMIT ?""",
            params + [max(0, limit) * 4],
        ).fetchall()

        # The WHERE over non-FTS columns happens in the JOIN; filter expired + tags here
        results: list[Memory] = []
        for row in rows:
            mem = self._row_to_memory(row)
            if mem is None or mem.expired:
                continue
            mem.score = self._blend_score(row["rank"], mem)
            results.append(mem)
            if len(results) >= limit:
                break
        return results

    # ----------------------------------------------------------------- utility

    def all(self, *, limit: int = 1000) -> list[Memory]:
        """Return all memories (newest first), excluding expired ones."""
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
            (max(0, limit),),
        ).fetchall()
        return [m for m in (self._row_to_memory(r) for r in rows) if m and not m.expired]

    def count(self) -> int:
        """Total number of stored memories (including expired)."""
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return int(row[0]) if row is not None else 0

    def stats(self) -> Stats:
        """Return aggregate statistics."""
        size = 0
        if self._path != ":memory:":
            try:
                size = Path(self._path).stat().st_size
            except OSError:
                size = 0
        return Stats(count=self.count(), db_path=self._path, db_size_bytes=size)

    def export(self) -> list[dict[str, Any]]:
        """Export all memories as a list of dicts (for backup/transfer)."""
        return [m.to_dict() for m in self.all(limit=10**9)]

    def import_(self, items: Iterable[dict[str, Any]]) -> int:
        """Bulk-insert memories from dicts (as produced by :meth:`export`).

        Existing ids are ignored (new ids are assigned). Returns count inserted.
        """
        added = 0
        for item in items:
            self.add(
                item["text"],
                metadata=item.get("metadata", {}),
                tags=item.get("tags", []),
                ttl=item.get("ttl"),
            )
            added += 1
        return added

    def gc(self) -> int:
        """Purge expired memories. Returns the number removed."""
        now = time.time()
        expired_ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM memories WHERE ttl IS NOT NULL AND ttl <= ?", (now,)
            ).fetchall()
        ]
        for mid in expired_ids:
            self._conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        self._conn.commit()
        return len(expired_ids)

    # --------------------------------------------------------- context manager

    def close(self) -> None:
        """Close the underlying database connection."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return f"MemoryStore(path={self._path!r}, count={self.count()})"

    # ----------------------------------------------------------------- helpers

    def _touch(self, memory_id: int) -> None:
        """Bump access stats for a retrieved memory."""
        now = time.time()
        self._conn.execute(
            "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
            (now, memory_id),
        )
        self._conn.commit()

    @staticmethod
    def _escape_query(query: str) -> str:
        """Make an FTS5 query safe while preserving prefix/wildcard support.

        Bare tokens like ``editor setup`` become ``"editor" "setup"`` which is
        a valid phrase/AND query. Tokens ending in ``*`` (prefix) are preserved.
        """
        tokens = []
        for tok in query.replace('"', " ").split():
            if tok.endswith("*"):
                tokens.append(tok)  # prefix query: keep as-is
            else:
                tokens.append(f'"{tok}"')
        return " ".join(tokens) if tokens else query

    @staticmethod
    def _row_to_memory(row: sqlite3.Row | None) -> Memory | None:
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        return Memory(
            id=int(row["id"]),
            text=row["text"],
            metadata=metadata,
            tags=tags,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            accessed_at=float(row["accessed_at"]),
            access_count=int(row["access_count"]),
            ttl=float(row["ttl"]) if row["ttl"] is not None else None,
        )

    def _blend_score(self, bm25_rank: float, mem: Memory) -> float:
        """Combine BM25 rank (lower is better) into a 0..1 score (higher better)."""
        # bm25() returns a negative value; more negative = more relevant.
        relevance = 1.0 / (1.0 + abs(bm25_rank))
        # Recency/access signal normalized to 0..1.
        now = time.time()
        age = max(0.0, now - mem.created_at)
        recency = 1.0 / (1.0 + age / 86400.0)  # half-life ~1 day
        access = min(1.0, mem.access_count / 10.0)
        signal = 0.6 * recency + 0.4 * access
        w = self._recency_weight
        return round(w * signal + (1.0 - w) * relevance, 6)
