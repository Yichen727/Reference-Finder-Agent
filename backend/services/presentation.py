"""Persist small UI metadata separately from research records and search logic."""

import json
import time

DEFAULT_RESEARCH_TITLE = "Creative Reference Research"


class PresentationStore:
    def __init__(self, repository):
        self.repository = repository

    def initialize(self):
        with self.repository.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS research_titles (run_id TEXT PRIMARY KEY, title TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reference_previews "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL)"
            )

    def title(self, run: dict) -> str:
        with self.repository.connection() as connection:
            existing = connection.execute(
                "SELECT title FROM research_titles WHERE run_id=?", (run["id"],)
            ).fetchone()
            if existing:
                return existing["title"]
            if run.get("planning_pending"):
                # Do not persist a temporary label before the edited brief gets its AI title.
                return (
                    "Research in progress" if run["status"] in {"queued", "running"}
                    else "Research did not complete"
                )
            # The planning response is already persisted with the research run.
            # Reading a title must never invoke a model or excerpt the original prompt.
            title = run["strategy"].get("research_title") or DEFAULT_RESEARCH_TITLE
            connection.execute("INSERT OR IGNORE INTO research_titles VALUES (?, ?)", (run["id"], title))
            return connection.execute(
                "SELECT title FROM research_titles WHERE run_id=?", (run["id"],)
            ).fetchone()["title"]

    def preview(self, key: str) -> dict | None:
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM reference_previews WHERE cache_key=? AND expires_at>?",
                (key, time.time()),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def rename(self, run_id: str, title: str):
        self.repository.get("run", run_id)
        with self.repository.connection() as connection:
            connection.execute(
                "INSERT INTO research_titles VALUES (?, ?) ON CONFLICT(run_id) "
                "DO UPDATE SET title=excluded.title",
                (run_id, title),
            )

    def save_preview(self, key: str, value: dict, ttl: int):
        with self.repository.connection() as connection:
            connection.execute(
                "INSERT INTO reference_previews VALUES (?, ?, ?) ON CONFLICT(cache_key) "
                "DO UPDATE SET payload=excluded.payload, expires_at=excluded.expires_at",
                (key, json.dumps(value), time.time() + ttl),
            )
