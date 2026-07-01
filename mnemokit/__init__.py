"""mnemokit — Lightweight persistent memory for AI agents and CLI tools.

SQLite + FTS5 full-text search. Zero external dependencies.

Example:
    from mnemokit import MemoryStore

    store = MemoryStore("memory.db")
    store.add("User prefers dark mode and Vim keybindings", tags=["preference"])
    hits = store.search("editor setup")
    for h in hits:
        print(h.text, h.score)
"""

from .store import MemoryStore, Memory, Stats

__version__ = "0.1.0"
__all__ = ["MemoryStore", "Memory", "Stats"]
