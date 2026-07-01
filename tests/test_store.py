"""Comprehensive test suite for mnemokit.MemoryStore."""
import json
import time

import pytest

from mnemokit import MemoryStore


@pytest.fixture
def store():
    s = MemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def populated_store(store):
    store.add("User prefers dark mode and Vim keybindings", tags=["preference", "editor"])
    store.add("The project uses Next.js 15 with Supabase for auth", tags=["stack", "backend"])
    store.add("Deployment target is Hetzner with Docker and nginx", tags=["infra", "deploy"])
    store.add("Always write tests with pytest and aim for 80% coverage", tags=["testing"])
    store.add("User likes Python type hints and dataclasses", tags=["preference", "python"])
    return store


class TestAddAndGet:
    def test_add_returns_id(self, store):
        mid = store.add("hello world")
        assert isinstance(mid, int)
        assert mid >= 1

    def test_add_empty_text_raises(self, store):
        with pytest.raises(ValueError):
            store.add("")
        with pytest.raises(ValueError):
            store.add("   ")

    def test_add_with_metadata_and_tags(self, store):
        mid = store.add("note", metadata={"k": "v"}, tags=["a", "b"])
        mem = store.get(mid)
        assert mem is not None
        assert mem.metadata == {"k": "v"}
        assert mem.tags == ["a", "b"]

    def test_add_with_ttl(self, store):
        mid = store.add("ephemeral", ttl=time.time() + 10)
        mem = store.get(mid)
        assert mem is not None
        assert mem.expired is False

    def test_get_nonexistent_returns_none(self, store):
        assert store.get(999999) is None

    def test_get_expired_memory_object(self, store):
        mid = store.add("old", ttl=time.time() - 5)
        mem = store.get(mid)
        assert mem is not None
        assert mem.expired is True

    def test_timestamps_set(self, store):
        before = time.time()
        mid = store.add("timestamped")
        mem = store.get(mid)
        assert mem.created_at >= before - 1
        assert mem.updated_at >= before - 1
        assert mem.accessed_at >= before - 1


class TestUpdateAndDelete:
    def test_update_text(self, store):
        mid = store.add("original")
        assert store.update(mid, text="updated") is True
        assert store.get(mid).text == "updated"

    def test_update_metadata_replaces(self, store):
        mid = store.add("x", metadata={"a": 1})
        store.update(mid, metadata={"b": 2})
        assert store.get(mid).metadata == {"b": 2}

    def test_update_nonexistent_returns_false(self, store):
        assert store.update(8888, text="nope") is False

    def test_delete(self, store):
        mid = store.add("to be deleted")
        assert store.delete(mid) is True
        assert store.get(mid) is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete(7777) is False

    def test_clear(self, populated_store):
        n = populated_store.count()
        removed = populated_store.clear()
        assert removed == n
        assert populated_store.count() == 0


class TestSearch:
    def test_basic_search(self, populated_store):
        hits = populated_store.search("dark mode")
        assert len(hits) >= 1
        assert "dark" in hits[0].text.lower() or "mode" in hits[0].text.lower()

    def test_search_returns_score(self, populated_store):
        hits = populated_store.search("python type hints")
        assert all(0.0 <= h.score <= 1.0 for h in hits)

    def test_search_limit(self, populated_store):
        hits = populated_store.search("the", limit=2)
        assert len(hits) <= 2

    def test_search_empty_query(self, store):
        assert store.search("") == []
        assert store.search("   ") == []

    def test_search_prefix_query(self, populated_store):
        hits = populated_store.search("deploy*")
        assert len(hits) >= 1  # matches "Deployment"

    def test_search_no_results(self, store):
        store.add("hello world")
        assert store.search("zzzznonexistent") == []

    def test_search_filter_by_tag(self, populated_store):
        hits = populated_store.search("preference", tags=["editor"])
        assert all("editor" in h.tags for h in hits)

    def test_search_filter_by_where(self, populated_store):
        # access_count starts at 0 for all -> none qualify
        hits = populated_store.search("user", where="access_count > 0")
        assert hits == []

    def test_search_excludes_expired(self, store):
        store.add("fresh content here")
        store.add("stale content here", ttl=time.time() - 1)
        hits = store.search("content")
        texts = [h.text for h in hits]
        assert "fresh content here" in texts
        assert "stale content here" not in texts


class TestUtility:
    def test_count(self, populated_store):
        assert populated_store.count() == 5

    def test_all(self, populated_store):
        everything = populated_store.all()
        assert len(everything) == 5

    def test_all_excludes_expired(self, store):
        store.add("alive")
        store.add("dead", ttl=time.time() - 1)
        assert len(store.all()) == 1

    def test_stats(self, tmp_path):
        p = tmp_path / "test.db"
        s = MemoryStore(str(p))
        s.add("a")
        st = s.stats()
        assert st.count == 1
        assert st.db_path == str(p)
        assert st.db_size_bytes > 0
        s.close()

    def test_stats_in_memory(self, store):
        st = store.stats()
        assert st.db_size_bytes == 0


class TestImportExport:
    def test_roundtrip(self, populated_store):
        data = populated_store.export()
        assert len(data) == 5
        store2 = MemoryStore(":memory:")
        n = store2.import_(data)
        assert n == 5
        hits = store2.search("vim")
        assert len(hits) >= 1
        store2.close()

    def test_export_is_json_serializable(self, populated_store):
        data = populated_store.export()
        assert json.dumps(data)  # must serialize cleanly


class TestGC:
    def test_gc_removes_expired(self, store):
        store.add("keep me")
        store.add("expire soon", ttl=time.time() + 1)
        time.sleep(1.1)
        removed = store.gc()
        assert removed == 1
        assert store.count() == 1

    def test_gc_nothing_expired(self, populated_store):
        assert populated_store.gc() == 0


class TestContextManager:
    def test_context_manager(self):
        with MemoryStore(":memory:") as s:
            s.add("inside")
            assert s.count() == 1
        # connection should be closed without error

    def test_close_idempotent(self, store):
        store.close()
        store.close()  # should not raise


class TestEdgeCases:
    def test_unicode_text(self, store):
        mid = store.add("用户喜欢深色模式和 Vim 键盘绑定", tags=["中文"])
        mem = store.get(mid)
        assert "深色模式" in mem.text
        hits = store.search("Vim")
        assert len(hits) >= 1

    def test_invalid_recency_weight(self):
        with pytest.raises(ValueError):
            MemoryStore(":memory:", recency_weight=1.5)
        with pytest.raises(ValueError):
            MemoryStore(":memory:", recency_weight=-0.1)

    def test_repr(self, store):
        r = repr(store)
        assert "MemoryStore" in r
        assert "count=0" in r

    def test_len(self, populated_store):
        assert len(populated_store) == 5

    def test_persistence_across_connections(self, tmp_path):
        p = str(tmp_path / "persist.db")
        s1 = MemoryStore(p)
        mid = s1.add("persistent memory")
        s1.close()
        s2 = MemoryStore(p)
        assert s2.count() == 1
        assert s2.get(mid).text == "persistent memory"
        s2.close()

    def test_metadata_with_nested_dict(self, store):
        mid = store.add("complex", metadata={"nested": {"deep": [1, 2, {"x": True}]}})
        assert store.get(mid).metadata == {"nested": {"deep": [1, 2, {"x": True}]}}

    def test_escape_query_handles_special_chars(self):
        q = MemoryStore._escape_query("foo bar")
        assert '"' in q
        assert "*" not in q

    def test_escape_query_preserves_prefix(self):
        q = MemoryStore._escape_query("dev* setup")
        assert "dev*" in q

    def test_corrupt_metadata_falls_back(self, store):
        store.add("ok")
        # Manually corrupt the metadata column
        store._conn.execute("UPDATE memories SET metadata = 'not json' WHERE id = 1")
        mem = store.get(1)
        assert mem.metadata == {}

    def test_memory_to_dict(self, store):
        mid = store.add("x", metadata={"a": 1}, tags=["t"])
        d = store.get(mid).to_dict()
        assert d["text"] == "x"
        assert d["metadata"] == {"a": 1}
        assert d["tags"] == ["t"]
