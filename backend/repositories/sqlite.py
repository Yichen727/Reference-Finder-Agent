import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from backend.core.errors import AppError
from backend.schemas.models import SearchRun


class Repository:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT, "
                "PRIMARY KEY (kind, id))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS submissions (key TEXT PRIMARY KEY, "
                "fingerprint TEXT NOT NULL, run_id TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deleted_research "
                "(run_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)"
            )
            for row in conn.execute("SELECT id, payload FROM records WHERE kind='run'").fetchall():
                data = json.loads(row["payload"])
                if data["status"] in {"queued", "running"}:
                    data.update(status="interrupted", stage="interrupted")
                    data["warnings"].append("Backend restarted; please submit a new run.")
                    conn.execute(
                        "UPDATE records SET payload=? WHERE kind='run' AND id=?",
                        (json.dumps(data), row["id"]),
                    )

    def put(self, kind: str, value: BaseModel):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(kind,id) "
                "DO UPDATE SET payload=excluded.payload",
                (kind, value.id, value.model_dump_json()),
            )

    @contextmanager
    def edit_run(self, run_id: str):
        """Serialize read/modify/write across backend instances, including additions."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM records WHERE kind='run' AND id=?", (run_id,)).fetchone()
            if not row or conn.execute(
                "SELECT 1 FROM deleted_research WHERE run_id=?", (run_id,)
            ).fetchone():
                raise AppError("not_found", "This research is no longer available.", 404)
            run = SearchRun.model_validate_json(row["payload"])
            yield run, conn
            conn.execute(
                "UPDATE records SET payload=? WHERE kind='run' AND id=?", (run.model_dump_json(), run_id)
            )

    def get(self, kind: str, record_id: str) -> dict:
        with self.connection() as conn:
            if (
                kind == "run"
                and conn.execute("SELECT 1 FROM deleted_research WHERE run_id=?", (record_id,)).fetchone()
            ):
                raise AppError("not_found", "This research is no longer available.", 404)
            row = conn.execute(
                "SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)
            ).fetchone()
        if row is None:
            raise AppError("not_found", "The requested record does not exist.", 404)
        value = json.loads(row["payload"])
        if kind == "run" and value.get("operation") == "add":
            with self.connection() as conn:
                if conn.execute(
                    "SELECT 1 FROM deleted_research WHERE run_id=?", (value.get("parent_run_id"),)
                ).fetchone():
                    raise AppError("not_found", "This research is no longer available.", 404)
        return value

    def recent_runs(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM records WHERE kind='run' AND NOT EXISTS "
                "(SELECT 1 FROM deleted_research WHERE run_id=records.id) "
                "AND COALESCE(json_extract(payload, '$.operation'), 'search') != 'add' "
                "ORDER BY rowid DESC LIMIT 30"
            )
            return [json.loads(row["payload"]) for row in rows]

    def research_actions(self, run_id: str) -> list[dict]:
        self.get("run", run_id)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM records WHERE kind='run' AND "
                "json_extract(payload, '$.parent_run_id')=? ORDER BY rowid DESC", (run_id,)
            )
            return [json.loads(row["payload"]) for row in rows]

    def delete_research(self, run_id: str):
        """Retain research, selections and files; revoke public access with a tombstone."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM records WHERE kind='run' AND id=?", (run_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "The requested research does not exist.", 404)
            if conn.execute("SELECT 1 FROM deleted_research WHERE run_id=?", (run_id,)).fetchone():
                return
            if json.loads(row["payload"])["status"] in {"queued", "running"}:
                raise AppError("research_busy", "Wait for this search to finish before deleting it.", 409)
            if conn.execute(
                "SELECT 1 FROM records WHERE kind='run' AND json_extract(payload, '$.parent_run_id')=? "
                "AND json_extract(payload, '$.status') IN ('queued','running')", (run_id,)
            ).fetchone():
                raise AppError(
                    "research_busy", "Wait for the research update to finish before deleting it.", 409
                )
            conn.execute(
                "INSERT INTO deleted_research VALUES (?, ?)",
                (run_id, datetime.now(timezone.utc).isoformat()),
            )

    def create_run(self, run: SearchRun, key: str, fingerprint: str, *, brief=None) -> tuple[dict, bool]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT fingerprint,run_id FROM submissions WHERE key=?", (key,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise AppError(
                        "idempotency_conflict", "Submission key was already used for another request.", 409
                    )
                if conn.execute(
                    "SELECT 1 FROM deleted_research WHERE run_id=?", (existing["run_id"],)
                ).fetchone():
                    raise AppError("not_found", "This research was deleted. Start new research.", 404)
                row = conn.execute(
                    "SELECT payload FROM records WHERE kind='run' AND id=?", (existing["run_id"],)
                ).fetchone()
                return json.loads(row["payload"]), False
            if run.parent_run_id:
                parent = conn.execute(
                    "SELECT payload FROM records WHERE kind='run' AND id=?", (run.parent_run_id,)
                ).fetchone()
                if not parent or conn.execute(
                    "SELECT 1 FROM deleted_research WHERE run_id=?", (run.parent_run_id,)
                ).fetchone():
                    raise AppError("not_found", "The original research is unavailable.", 404)
                parent = json.loads(parent["payload"])
                if parent["mode"] != "live" or parent["status"] not in {"completed", "partial"}:
                    raise AppError("run_not_ready", "Open completed live research before updating it.", 409)
                if parent.get("operation") == "add":
                    raise AppError(
                        "invalid_parent", "Update the original research, not an additional batch.", 409
                    )
                if conn.execute(
                    "SELECT 1 FROM records WHERE kind='run' AND json_extract(payload, '$.parent_run_id')=? "
                    "AND json_extract(payload, '$.status') IN ('queued','running')", (run.parent_run_id,)
                ).fetchone():
                    raise AppError("research_busy", "A research update is already in progress.", 409)
            pending = conn.execute(
                "SELECT COUNT(*) FROM records WHERE kind='run' AND "
                "json_extract(payload, '$.status') IN ('queued','running')"
            ).fetchone()[0]
            if pending >= 8:
                raise AppError("queue_full", "Too many pending runs. Please try again later.", 429)
            conn.execute("INSERT INTO submissions VALUES (?,?,?)", (key, fingerprint, run.id))
            if brief is not None:
                conn.execute("INSERT INTO records VALUES ('brief',?,?)", (brief.id, brief.model_dump_json()))
            conn.execute("INSERT INTO records VALUES ('run',?,?)", (run.id, run.model_dump_json()))
            return run.model_dump(mode="json"), True
