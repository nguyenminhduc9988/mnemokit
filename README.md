# mnemokit

[![CI](https://github.com/nguyenminhduc9988/mnemokit/actions/workflows/ci.yml/badge.svg)](https://github.com/nguyenminhduc9988/mnemokit/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-94%25-brightgreen)](https://github.com/nguyenminhduc9988/mnemokit/actions)
[![PyPI](https://img.shields.io/pypi/v/mnemokit.svg)](https://pypi.org/project/mnemokit/)
[![Python](https://img.shields.io/pypi/pyversions/mnemokit.svg)](https://pypi.org/project/mnemokit/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Lightweight persistent memory for AI memory for AI agents and CLI tools.**
> SQLite + FTS5 full-text search. Zero external dependencies.

---

## Why mnemokit?

Building an AI agent or CLI tool that needs to **remember things across sessions**?
- User preferences and conversation context
- Project facts and technical decisions
- Code snippets and error solutions
- Task history and learned patterns

You don't need a database server, Redis, or a vector store. You need a tiny, durable, searchable key-value store with **full-text search** built in.

```
┌─────────────────────────────────────────────────────────────┐
│  mnemokit: < 500 LOC, 0 deps, pure Python + SQLite stdlib  │
├─────────────────────────────────────────────────────────────┤
│  ✅ FTS5 full-text search (BM25 + recency ranking)          │
│  ✅ Metadata & tags (JSON, filterable)                      │
│  ✅ TTL / auto-expiry                                       │
│  ✅ Thread-safe (RLock)                                     │
│  ✅ Export/import (JSON backup)                             │
│  ✅ In-memory or file-backed                                │
│  ✅ 94% test coverage, typed                                │
└─────────────────────────────────────────────────────────────┘
```

---

## Install

```bash
pip install mnemokit
# or
uv add mnemokit
```

Requires **Python 3.11+** (standard library only — `sqlite3`, `json`, `threading`, `dataclasses`, `pathlib`).

---

## Quick Start

```python
from mnemokit import MemoryStore

# File-backed (persistent across runs)
store = MemoryStore("agent_memory.db")

# Or ephemeral in-memory
# store = MemoryStore(":memory:")

# Add memories with metadata and tags
store.add(
    "User prefers dark mode and Vim keybindings",
    metadata={"source": "onboarding", "confidence": 0.9},
    tags=["preference", "editor"]
)

store.add(
    "Project uses Next.js 15 + Supabase for auth",
    tags=["stack", "backend"]
)

# Full-text search — ranked by relevance + recency
hits = store.search("editor setup", limit=3)
for h in hits:
    print(f"[{h.score:.2f}] {h.text}  (tags: {h.tags})")
# [0.87] User prefers dark mode and Vim keybindings  (tags: ['preference', 'editor'])

# Filter by tag
hits = store.search("preference", tags=["editor"])

# Get / update / delete
mem = store.get(hits[0].id)
store.update(mem.id, text="User prefers dark mode, Vim, AND tmux")
store.delete(mem.id)

# Stats & export
print(store.stats())       # Stats(count=42, db_path='...', db_size_bytes=8192)
backup = store.export()    # list[dict] — save to JSON file
store.import_(backup)      # restore into another store

# Cleanup
store.close()  # or use `with MemoryStore(...) as store:`
```

---

## API Reference

### `MemoryStore(path, *, recency_weight=0.15)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str \| Path` | `":memory:"` | SQLite DB file. Use `":memory:"` for ephemeral. |
| `recency_weight` | `float` | `0.15` | 0..1 weight blending recency/access into search score. |

### `add(text, *, metadata=None, tags=None, ttl=None) -> int`
Insert a memory. Returns the new `id`.

### `get(id) -> Memory | None`
Retrieve a memory by id (updates access stats).

### `update(id, *, text=None, metadata=None, tags=None) -> bool`
Update fields. Returns `True` if changed.

### `delete(id) -> bool`
Delete by id. Returns `True` if removed.

### `clear() -> int`
Delete all memories. Returns count removed.

### `search(query, *, limit=5, tags=None, where=None) -> list[Memory]`
**Full-text search** with FTS5 query syntax:
- Plain words: `editor setup` → AND of both terms
- Phrases: `"dark mode"`
- Prefix: `develop*`
- Boolean: `python AND (vim OR emacs)`, `NOT windows`

Results are `Memory` objects with a blended `score` (0..1, higher = more relevant).

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `str` | FTS5 query string |
| `limit` | `int` | Max results |
| `tags` | `Iterable[str]` | Only memories containing ALL these tags |
| `where` | `str` | Raw SQL WHERE clause on `memories` table |

### `all(limit=1000) -> list[Memory]`
All memories, newest first (excludes expired).

### `count() -> int`
Total memories (includes expired).

### `stats() -> Stats`
Aggregate info: count, db path, file size.

### `export() -> list[dict]`
JSON-serializable backup of all memories.

### `import_(items) -> int`
Bulk-insert from exported format. Returns count added.

### `gc() -> int`
Purge expired memories. Returns count removed.

---

## Memory Object

```python
@dataclass
class Memory:
    id: int
    text: str
    metadata: dict[str, Any]
    tags: list[str]
    created_at: float
    updated_at: float
    accessed_at: float
    access_count: int
    ttl: float | None
    score: float        # set by search()
    
    @property
    def expired(self) -> bool:
        return self.ttl is not None and self.ttl <= time.time()
    
    def to_dict(self) -> dict[str, Any]: ...
```

---

## Ranking Explained

Search scores blend two signals:

```
score = (1 - w) * relevance + w * (0.6 * recency + 0.4 * access_freq)
```

- **relevance**: BM25 from FTS5 (how well query matches text + tags)
- **recency**: exponential decay, half-life ~1 day
- **access_freq**: how often this memory was retrieved (capped)
- **w** (`recency_weight`): default `0.15` — mostly relevance, slight recency boost

---

## Use Cases

| Scenario | How mnemokit Helps |
|----------|-------------------|
| **AI coding agent** | Remembers project stack, conventions, fixed bugs across sessions |
| **CLI productivity tool** | Stores user aliases, frequent commands, project contexts |
| **Chatbot with long-term memory** | Persists user preferences, facts, conversation threads |
| **Automation script** | Caches API responses, learned selectors, error fixes |
| **Research assistant** | Stores extracted facts with source metadata, full-text searchable |

---

## Project Structure

```
mnemokit/
├── mnemokit/
│   ├── __init__.py       # exports MemoryStore, Memory, Stats
│   └── store.py          # core implementation (~400 LOC)
├── tests/
│   └── test_store.py     # 43 tests, 94% coverage
├── pyproject.toml
├── LICENSE (MIT)
└── README.md
```

---

## Contributing

```bash
git clone https://github.com/nguyenminhduc9988/mnemokit
cd mnemokit
uv sync --dev  # or pip install -e .[dev]
pytest -q --cov=mnemokit
```

- All tests must pass
- Coverage must stay ≥ 90%
- Type hints on all public APIs (pyright clean)

---

## License

MIT © 2026 Duc Nguyen — see [LICENSE](LICENSE)

---

## Related

Part of the **Hermes agent ecosystem** — tools for autonomous AI agents.
See also: [hermes-tool-guard](https://github.com/nguyenminhduc9988/hermes-tool-guard) (rate-limiting middleware), [agent-loop-lite](https://github.com/nguyenminhduc9988/agent-loop-lite) (Plan-Act-Observe loop).