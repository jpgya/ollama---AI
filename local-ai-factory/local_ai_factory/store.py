from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, data_root: Path):
        self.root = data_root
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("approved", "rejected", "experiments", "models"):
            (self.root / "artifacts" / name).mkdir(parents=True, exist_ok=True)
        for name in ("logs", "experiments", "datasets", "reports"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "factory.sqlite3"
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, task_id TEXT, status TEXT NOT NULL,
                    artifact_hash TEXT, model TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, duration_seconds REAL, evaluation_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
                    path TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                    model TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def upsert_task(self, task_id: str, title: str, description: str, status: str = "pending") -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO tasks(id,title,description,status,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                description=excluded.description, updated_at=excluded.updated_at""",
                (task_id, title, description, status, utc_now()),
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY updated_at DESC")]

    def pending_task(self, retry_limit: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE status='pending' AND attempts <= ? ORDER BY updated_at LIMIT 1",
                (retry_limit,),
            ).fetchone()
            return None if row is None else dict(row)

    def task_update(self, task_id: str, status: str, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status=?, attempts=attempts+?, last_error=?, updated_at=? WHERE id=?",
                (status, int(status == "running"), error, utc_now(), task_id),
            )

    def add_run(
        self,
        run_id: str,
        task_id: str | None,
        status: str,
        artifact_hash: str | None,
        model: str,
        started_at: str,
        duration: float,
        evaluation: dict[str, Any],
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, task_id, status, artifact_hash, model, started_at, utc_now(),
                    duration, json.dumps(evaluation, ensure_ascii=False),
                ),
            )

    def add_artifact(
        self, artifact_id: str, digest: str, status: str, path: Path, metadata: dict[str, Any]
    ) -> bool:
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
                    (
                        artifact_id, digest, status, str(path),
                        json.dumps(metadata, ensure_ascii=False), utc_now(),
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def add_event(
        self,
        event_type: str,
        model: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO activity_events
                (event_type, model, message, details_json, created_at)
                VALUES(?,?,?,?,?)""",
                (
                    event_type,
                    model,
                    message,
                    json.dumps(details or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM activity_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["details"] = json.loads(event.pop("details_json"))
            events.append(event)
        return events

    def report(self) -> dict[str, Any]:
        with self.connect() as db:
            result = db.execute(
                """SELECT COUNT(*) AS total,
                SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected,
                AVG(duration_seconds) AS avg_duration
                FROM runs"""
            ).fetchone()
            return {
                "total": result["total"] or 0,
                "approved": result["approved"] or 0,
                "rejected": result["rejected"] or 0,
                "avg_duration_seconds": result["avg_duration"],
            }

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
