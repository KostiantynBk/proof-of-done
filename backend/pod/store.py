"""SQLite persistence and per-run, hash-chained event journals.

A chain detects changes to retained rows; it is not an authenticated signature and
cannot independently detect deletion of an entire journal or its final entries.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _event_hash(previous: str, run_id: str, ts: str, event_type: str, payload: Any) -> str:
    body = _json({"run_id": run_id, "ts": ts, "type": event_type, "payload": payload})
    return hashlib.sha256((previous + body).encode("utf-8")).hexdigest()


class Store:
    """Thread-safe store. Separate instances also serialize writes via SQLite."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=30000")
            self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError(f"Unsupported database schema version: {version}")
        if version == 0:
            self._db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    project TEXT NOT NULL, summary_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL, ts TEXT NOT NULL,
                    type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL, hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq);
                CREATE TABLE IF NOT EXISTS sandbox_runs (
                    sandbox_id TEXT PRIMARY KEY, run_id TEXT, purpose TEXT NOT NULL,
                    result_json TEXT NOT NULL, started_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sandboxes_started ON sandbox_runs(started_at DESC);
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL,
                    sha256 TEXT NOT NULL, PRIMARY KEY(run_id, name)
                );
                PRAGMA user_version=1;
                COMMIT;
            """)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def create_run(
        self, run_id: str, project: str, status: str = "pending", summary: Any = None
    ) -> dict[str, Any]:
        created_at = _now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
                (run_id, created_at, status, project, _json(summary if summary is not None else {})),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        return {**dict(row), "summary": json.loads(row["summary_json"])}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return self._run(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM runs ORDER BY created_at DESC, id").fetchall()
            return [self._run(row) for row in rows]

    def update_run(self, run_id: str, *, status: str | None = None, summary: Any = None) -> bool:
        assignments, values = [], []
        if status is not None:
            assignments.append("status=?")
            values.append(status)
        if summary is not None:
            assignments.append("summary_json=?")
            values.append(_json(summary))
        if not assignments:
            return self.get_run(run_id) is not None
        with self._lock, self._db:
            cursor = self._db.execute(
                f"UPDATE runs SET {', '.join(assignments)} WHERE id=?", (*values, run_id)
            )
            return cursor.rowcount > 0

    def delete_run(self, run_id: str) -> bool:
        # Keep journal, artifacts, and sandbox receipts intact for auditability.
        with self._lock, self._db:
            return self._db.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount > 0

    def append_event(
        self, run_id: str, type: str, payload: Any, ts: str | None = None
    ) -> dict[str, Any]:
        ts = ts if ts is not None else _now()
        payload_json = _json(payload)
        payload_snapshot = json.loads(payload_json)
        with self._lock:
            try:
                # Acquire the write lock before reading the head, including across processes.
                self._db.execute("BEGIN IMMEDIATE")
                row = self._db.execute(
                    "SELECT hash FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)
                ).fetchone()
                prev_hash = row["hash"] if row else GENESIS
                digest = _event_hash(prev_hash, run_id, ts, type, payload_snapshot)
                cursor = self._db.execute(
                    "INSERT INTO events(run_id, ts, type, payload_json, prev_hash, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, ts, type, payload_json, prev_hash, digest),
                )
                seq = cursor.lastrowid
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
        return {"seq": seq, "run_id": run_id, "ts": ts, "type": type, "payload": payload_snapshot,
                "payload_json": payload_json, "prev_hash": prev_hash, "hash": digest}

    def list_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after_seq)
            ).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def verify_chain(self, run_id: str) -> bool:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()
            previous = GENESIS
            try:
                for row in rows:
                    if row["prev_hash"] != previous:
                        return False
                    expected = _event_hash(previous, row["run_id"], row["ts"], row["type"],
                                           json.loads(row["payload_json"]))
                    if row["hash"] != expected:
                        return False
                    previous = row["hash"]
            except (ValueError, TypeError):
                return False
            return True

    def save_sandbox_result(self, result: Any) -> None:
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        result = dict(result)
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO sandbox_runs "
                "(sandbox_id, run_id, purpose, result_json, started_at) VALUES (?, ?, ?, ?, ?)",
                (result["sandbox_id"], result.get("run_id"), result["purpose"],
                 _json(result), result["started_at"]),
            )

    def recent_sandboxes(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        with self._lock:
            rows = self._db.execute(
                "SELECT result_json FROM sandbox_runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [json.loads(row["result_json"]) for row in rows]

    def put_artifact(self, run_id: str, name: str, content: str | bytes) -> str:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        digest = hashlib.sha256(raw).hexdigest()
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO artifacts(run_id, name, content, sha256) VALUES (?, ?, ?, ?)",
                (run_id, name, content, digest),
            )
        return digest

    def get_artifact(self, run_id: str, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT run_id, name, content, sha256 FROM artifacts WHERE run_id=? AND name=?",
                (run_id, name),
            ).fetchone()
            return dict(row) if row else None


# Descriptive alias for callers that prefer the explicit name.
SQLiteStore = Store
