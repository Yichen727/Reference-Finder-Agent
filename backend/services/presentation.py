"""Persist small UI metadata separately from research records and search logic."""

import json
import re
import time


def research_title(prompt: str) -> str:
    """Produce a short extractive label without another model request."""
    text = re.sub(r"\s+", " ", prompt).strip()
    text = re.sub(
        r"^(?:(?:please\s+)?(?:create|find|research|explore|show|develop|make|give me|i need|we need)\s+)",
        "",
        text,
        flags=re.I,
    )
    topic = re.split(r"\b(?:about|exploring|focused on|focusing on)\s+", text, maxsplit=1, flags=re.I)[-1]
    topic = re.split(r"[.!?;:\n]|\s+(?:with|using|aimed at|targeting)\s+", topic, maxsplit=1, flags=re.I)[0]
    topic = re.sub(r"^(?:a|an|the)\s+", "", topic, flags=re.I)
    words = [word[:24] for word in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", topic)[:6]]
    if not words:
        words = ["Creative", "Reference", "Research"]
    elif len(words) == 1:
        words = [words[0], "Creative", "References"]
    elif len(words) == 2:
        words.append("References")
    small = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
    return " ".join(
        word if word.isupper() else word.lower() if index and word.lower() in small else word.capitalize()
        for index, word in enumerate(words)
    )


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
            row = connection.execute(
                "SELECT payload FROM records WHERE kind='brief' AND id=?", (run["brief_id"],)
            ).fetchone()
            prompt = json.loads(row["payload"])["concept"] if row else run["strategy"]["summary"]
            connection.execute(
                "INSERT OR IGNORE INTO research_titles VALUES (?, ?)", (run["id"], research_title(prompt))
            )
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
