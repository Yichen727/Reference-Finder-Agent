import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from backend.core.errors import AppError


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

    def get(self, kind: str, record_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)
            ).fetchone()
        if row is None:
            raise AppError("not_found", "The requested record does not exist.", 404)
        return json.loads(row["payload"])

    def recent_runs(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT payload FROM records WHERE kind='run' ORDER BY rowid DESC LIMIT 30")
            return [json.loads(row["payload"]) for row in rows]

    def create_run(self, run: BaseModel, key: str, fingerprint: str) -> tuple[dict, bool]:
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
                row = conn.execute(
                    "SELECT payload FROM records WHERE kind='run' AND id=?", (existing["run_id"],)
                ).fetchone()
                return json.loads(row["payload"]), False
            pending = conn.execute(
                "SELECT COUNT(*) FROM records WHERE kind='run' AND "
                "json_extract(payload, '$.status') IN ('queued','running')"
            ).fetchone()[0]
            if pending >= 8:
                raise AppError("queue_full", "Too many pending runs. Please try again later.", 429)
            conn.execute("INSERT INTO submissions VALUES (?,?,?)", (key, fingerprint, run.id))
            conn.execute("INSERT INTO records VALUES ('run',?,?)", (run.id, run.model_dump_json()))
            return run.model_dump(mode="json"), True
