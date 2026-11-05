import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pod.store import GENESIS, Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "nested" / "pod.sqlite")
    yield db
    db.close()


def test_run_crud_and_reopen(tmp_path):
    path = tmp_path / "state.db"
    db = Store(path)
    run = db.create_run("r1", "demo:refundly")
    assert run["status"] == "pending" and run["summary"] == {}
    assert db.update_run("r1", status="running", summary={"count": 2})
    assert not db.update_run("missing", status="running")
    db.close()
    db = Store(path)
    assert db.get_run("r1")["summary"] == {"count": 2}
    assert [r["id"] for r in db.list_runs()] == ["r1"]
    assert db.delete_run("r1") and not db.delete_run("r1")
    assert db.get_run("r1") is None
    db.close()


def test_chain_is_canonical_per_run_and_paginates(store):
    one = store.append_event("a", "stage", {"z": 1, "a": "é"}, ts="t1")
    other = store.append_event("b", "start", {}, ts="t2")
    two = store.append_event("a", "end", [1, 2], ts="t3")
    canonical = json.dumps({"run_id": "a", "ts": "t1", "type": "stage",
                            "payload": {"z": 1, "a": "é"}},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert one["hash"] == hashlib.sha256((GENESIS + canonical).encode()).hexdigest()
    assert one["prev_hash"] == other["prev_hash"] == GENESIS
    assert two["prev_hash"] == one["hash"]
    assert [e["seq"] for e in store.list_events("a", one["seq"])] == [two["seq"]]
    assert store.verify_chain("a") and store.verify_chain("b")


@pytest.mark.parametrize("field,value", [("payload_json", '{"changed":true}'),
                                         ("payload_json", 'not json'),
                                         ("prev_hash", "fake"), ("hash", "fake")])
def test_tampering_breaks_chain(store, field, value):
    store.append_event("r", "start", {"ok": True})
    event = store.append_event("r", "end", {})
    with store._db:
        store._db.execute(f"UPDATE events SET {field}=? WHERE seq=?", (value, event["seq"]))
    assert not store.verify_chain("r")


def test_concurrent_appends_across_connections(tmp_path):
    path = tmp_path / "events.db"
    first, second = Store(path), Store(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: (first if n % 2 else second).append_event("r", "n", {"n": n}),
                      range(100)))
    assert len(first.list_events("r")) == 100
    assert first.verify_chain("r")
    first.close()
    second.close()


def test_sandbox_and_artifact_roundtrip(store):
    store.save_sandbox_result({"sandbox_id": "s1", "run_id": None, "purpose": "playground",
                               "started_at": "2026-01-01", "outcome": "passed"})
    store.save_sandbox_result({"sandbox_id": "s2", "purpose": "baseline",
                               "started_at": "2026-01-02", "outcome": "error"})
    assert [r["sandbox_id"] for r in store.recent_sandboxes(1)] == ["s2"]
    assert store.recent_sandboxes(0) == []
    with pytest.raises(ValueError):
        store.recent_sandboxes(-1)
    sha = store.put_artifact("r", "report.md", "Hello é")
    artifact = store.get_artifact("r", "report.md")
    assert artifact["content"] == "Hello é"
    assert sha == artifact["sha256"] == hashlib.sha256("Hello é".encode()).hexdigest()
    store.put_artifact("r", "report.md", b"\x00\xff")
    assert store.get_artifact("r", "report.md")["content"] == b"\x00\xff"
    assert store.get_artifact("r", "missing") is None


def test_wal_and_future_schema_rejected(tmp_path):
    path = tmp_path / "store.db"
    store = Store(path)
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=2")
    with pytest.raises(RuntimeError, match="Unsupported database schema"):
        Store(path)


def test_mutable_payload_snapshot_survives_lock_wait(store, monkeypatch):
    import threading

    from pod import store as store_module

    payload = {"progress": {"count": 1}}
    captured = threading.Event()
    original_json = store_module._json

    def observed_json(value):
        result = original_json(value)
        if value is payload:
            captured.set()
        return result

    monkeypatch.setattr(store_module, "_json", observed_json)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store._lock:
            future = pool.submit(store.append_event, "r", "progress", payload)
            assert captured.wait(timeout=5)
            payload["progress"]["count"] = 2
        event = future.result(timeout=5)
    assert event["payload"] == {"progress": {"count": 1}}
    payload["progress"]["count"] = 3
    assert event["payload"] == store.list_events("r")[0]["payload"]
    assert store.verify_chain("r")
