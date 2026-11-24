"""Global favorites and named packs survive research deletion and backend restarts."""

import hashlib
import json

from backend.core.errors import AppError
from backend.schemas.library import ReferencePack, ReferenceSnapshot, SavedReference
from backend.schemas.models import SearchRun, now
from backend.services.ranking import same_work
from backend.services.source_policy import canonical_url


class ReferenceLibrary:
    def __init__(self, workflow, thumbnails):
        self.workflow, self.repository, self.thumbnails = workflow, workflow.repository, thumbnails

    def initialize(self):
        with self.repository.connection() as conn:
            for table in ("saved_references", "reference_packs", "library_previews"):
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS library_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS library_commands "
                "(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, pack_id TEXT NOT NULL)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS deleted_packs (id TEXT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS imported_research_packs "
                "(run_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS research_packs "
                "(run_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL, pending_name INTEGER NOT NULL)"
            )
        self.import_legacy_packs()

    def create_research_pack(self, conn, run):
        """Called inside the new-run transaction; retries never create another pack."""
        if run.mode != "live" or run.operation == "add":
            return
        pack = ReferencePack(name=(run.strategy.research_title or "New Research Pack")[:100])
        self.save(conn, "reference_packs", pack)
        conn.execute(
            "INSERT INTO research_packs VALUES (?,?,?)", (run.id, pack.id, int(run.planning_pending)),
        )
        conn.execute("UPDATE library_state SET value=? WHERE key='current_pack'", (pack.id,))

    def sync_research_pack_names(self, conn):
        """Reuse the completed planning title, while respecting manual pack names."""
        if not conn.execute("SELECT 1 FROM research_packs WHERE pending_name=1 LIMIT 1").fetchone():
            return
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT rp.run_id, rp.pack_id, records.payload FROM research_packs rp "
            "JOIN records ON records.kind='run' AND records.id=rp.run_id "
            "WHERE rp.pending_name=1 AND NOT EXISTS (SELECT 1 FROM deleted_packs WHERE id=rp.pack_id)"
        ).fetchall()
        for row in rows:
            run = json.loads(row["payload"])
            if run.get("planning_pending") or not run["strategy"].get("research_title"):
                continue
            title = conn.execute(
                "SELECT title FROM research_titles WHERE run_id=?", (row["run_id"],),
            ).fetchone()
            name = (title["title"] if title else run["strategy"]["research_title"])[:100]
            pack = self.pack(conn, row["pack_id"])
            if pack.name != name:
                pack.name = name
                self.touch(pack)
                self.save(conn, "reference_packs", pack)
            conn.execute("UPDATE research_packs SET pending_name=0 WHERE run_id=?", (row["run_id"],))

    @staticmethod
    def save(conn, table, value):
        conn.execute(
            f"INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (value.id, value.model_dump_json()),
        )

    @staticmethod
    def saved(conn, saved_id):
        row = conn.execute("SELECT payload FROM saved_references WHERE id=?", (saved_id,)).fetchone()
        if not row:
            raise AppError("reference_not_found", "This saved reference is unavailable.", 404)
        return SavedReference.model_validate_json(row["payload"])

    @staticmethod
    def pack(conn, pack_id):
        row = conn.execute("SELECT payload FROM reference_packs WHERE id=?", (pack_id,)).fetchone()
        if not row or conn.execute("SELECT 1 FROM deleted_packs WHERE id=?", (pack_id,)).fetchone():
            raise AppError("pack_not_found", "This pack is no longer available.", 404)
        return ReferencePack.model_validate_json(row["payload"])

    @staticmethod
    def check_version(pack, expected):
        if pack.version != expected:
            raise AppError(
                "pack_conflict", "This pack changed in another session. Load the latest pack.", 409
            )

    @staticmethod
    def touch(pack):
        pack.version += 1
        pack.updated_at = now()
        ReferencePack.model_validate(pack.model_dump())

    @staticmethod
    def all_saved(conn):
        return [
            SavedReference.model_validate_json(row["payload"])
            for row in conn.execute("SELECT payload FROM saved_references ORDER BY rowid DESC")
        ]

    @staticmethod
    def match(reference, saved):
        def urls(item):
            values = [item.source_url, *item.alternate_urls]
            if item.page_check and item.page_check.work_page:
                values += [item.page_check.final_url, item.page_check.canonical_url]
            return {url for value in values if value and (url := canonical_url(str(value)))}

        reference_urls = urls(reference)
        return next(
            (
                item
                for item in saved
                if any(
                    reference_urls & urls(snapshot.reference) or same_work(reference, snapshot.reference)
                    for snapshot in item.snapshots
                )
            ),
            None,
        )

    def snapshot(self, locator, conn):
        if locator.saved_reference_id:
            saved = self.saved(conn, locator.saved_reference_id)
            return saved.snapshots[saved.favorite_snapshot].model_copy(deep=True)
        run = self.workflow.run(locator.run_id)
        if run.mode != "live" or run.status not in {"completed", "partial"}:
            raise AppError("run_not_ready", "Save references from completed live research.", 409)
        item = next((item for item in run.results if item.id == locator.reference_id), None)
        if item is None:
            raise AppError("reference_not_found", "This reference does not belong to the research.", 404)
        item = item.model_copy(deep=True)
        if locator.annotation:
            item.annotation = locator.annotation.model_copy(deep=True)
        elif run.selection and item.id in run.selection.annotation_overrides:
            item.annotation = run.selection.annotation_overrides[item.id].model_copy(deep=True)
        anchors = set(item.evidence_ids)
        if item.assessment:
            anchors.update(item.assessment.evidence_ids)
        source_run = run
        for batch_id in reversed(run.appended_batch_ids):
            row = conn.execute(
                "SELECT payload FROM records WHERE kind='run' AND id=?", (batch_id,)
            ).fetchone()
            if row:
                raw = json.loads(row["payload"])
                if (
                    raw.get("operation") == "add"
                    and raw.get("parent_run_id") == run.id
                    and item.id in raw.get("added_reference_ids", [])
                ):
                    source_run = SearchRun.model_validate(raw)
                    break
        return ReferenceSnapshot(
            run_id=run.id,
            batch_run_id=source_run.id if source_run.id != run.id else None,
            reference_id=item.id,
            reference=item,
            evidence=[e.model_copy(deep=True) for e in run.evidence if e.id in anchors],
            brief=self.workflow.brief(source_run.brief_id),
            strategy=source_run.strategy.model_copy(deep=True),
            warnings=list(source_run.warnings),
        )

    def register(self, conn, snapshot):
        saved = self.match(snapshot.reference, self.all_saved(conn))
        if saved is None:
            saved = SavedReference(snapshots=[snapshot])
        index = next((i for i, old in enumerate(saved.snapshots) if old == snapshot), None)
        if index is None:
            saved.snapshots.append(snapshot)
            index = len(saved.snapshots) - 1
        self.save(conn, "saved_references", saved)
        preview = self.thumbnails.store.preview(self.thumbnails.cache_key(snapshot.reference))
        if preview and preview.get("image"):
            conn.execute(
                "INSERT INTO library_previews VALUES (?,?) ON CONFLICT(id) DO NOTHING",
                (self.preview_key(snapshot), json.dumps(preview)),
            )
        return saved, index

    @staticmethod
    def preview_key(snapshot):
        item = snapshot.reference
        metadata = item.thumbnail.model_dump(mode="json") if item.thumbnail else None
        identity = [snapshot.run_id, snapshot.reference_id, str(item.source_url), metadata]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def import_legacy_packs(self):
        from backend.schemas.library import ReferenceLocator

        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT payload FROM records WHERE kind='run' AND "
                "NOT EXISTS (SELECT 1 FROM deleted_research WHERE run_id=records.id) "
                "AND NOT EXISTS (SELECT 1 FROM imported_research_packs WHERE run_id=records.id) "
                "AND NOT EXISTS (SELECT 1 FROM research_packs WHERE run_id=records.id) "
                "ORDER BY rowid"
            ).fetchall()
            imported = []
            for row in rows:
                raw = json.loads(row["payload"])
                if (
                    raw.get("mode") != "live"
                    or raw.get("status") not in {"completed", "partial"}
                    or not (raw.get("selection") or {}).get("ordered_ids")
                    or raw.get("operation") == "add"
                ):
                    continue
                run = SearchRun.model_validate(raw)
                title = conn.execute("SELECT title FROM research_titles WHERE run_id=?", (run.id,)).fetchone()
                name = title["title"] if title else run.strategy.research_title or "Saved Research Pack"
                pack = ReferencePack(name=name, imported_run_id=run.id)
                for reference_id in run.selection.ordered_ids:
                    snapshot = self.snapshot(ReferenceLocator(run_id=run.id, reference_id=reference_id), conn)
                    saved, _ = self.register(conn, snapshot)
                    if saved.id not in pack.references:
                        pack.ordered_ids.append(saved.id)
                        pack.references[saved.id] = snapshot
                self.save(conn, "reference_packs", pack)
                conn.execute("INSERT INTO imported_research_packs VALUES (?,?)", (run.id, pack.id))
                imported.append(pack)
            current = conn.execute("SELECT value FROM library_state WHERE key='current_pack'").fetchone()
            if current is None:
                pack = imported[-1] if imported else ReferencePack(name="My Reference Pack")
                self.save(conn, "reference_packs", pack)
                conn.execute("INSERT INTO library_state VALUES ('current_pack',?)", (pack.id,))

    def state(self, run_id=None):
        with self.repository.connection() as conn:
            self.sync_research_pack_names(conn)
            saved = self.all_saved(conn)
            current_id = conn.execute("SELECT value FROM library_state WHERE key='current_pack'").fetchone()[
                0
            ]
            current = self.pack(conn, current_id)
            packs = [
                ReferencePack.model_validate_json(row["payload"])
                for row in conn.execute(
                    "SELECT payload FROM reference_packs WHERE NOT EXISTS "
                    "(SELECT 1 FROM deleted_packs WHERE id=reference_packs.id) ORDER BY rowid DESC"
                )
            ]
            mapping = {}
            for item in saved:
                for snapshot in item.snapshots:
                    mapping[f"{snapshot.run_id}:{snapshot.reference_id}"] = item.id
            if run_id:
                for reference in self.workflow.run(run_id).results:
                    match = self.match(reference, saved)
                    if match:
                        mapping[f"{run_id}:{reference.id}"] = match.id
            return {
                "favorites": [item.model_dump(mode="json") for item in saved if item.favorite],
                "current_pack": current.model_dump(mode="json"),
                "packs": [
                    {"id": p.id, "name": p.name, "version": p.version, "count": len(p.ordered_ids)}
                    for p in packs
                ],
                "reference_index": mapping,
            }

    def favorite(self, locator):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = self.snapshot(locator, conn)
            saved, index = self.register(conn, snapshot)
            if not saved.favorite:
                saved.favorite_snapshot = index
            saved.favorite = True
            self.save(conn, "saved_references", saved)
            return saved

    def unfavorite(self, saved_id):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saved = self.saved(conn, saved_id)
            saved.favorite = False
            self.save(conn, "saved_references", saved)

    def create_pack(self, value):
        fingerprint = hashlib.sha256(value.name.encode()).hexdigest()
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT * FROM library_commands WHERE key=?", (value.idempotency_key,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise AppError("idempotency_conflict", "This creation key belongs to another pack.", 409)
                return self.pack(conn, old["pack_id"])
            pack = ReferencePack(name=value.name)
            self.save(conn, "reference_packs", pack)
            conn.execute(
                "INSERT INTO library_commands VALUES (?,?,?)", (value.idempotency_key, fingerprint, pack.id)
            )
            conn.execute("UPDATE library_state SET value=? WHERE key='current_pack'", (pack.id,))
            return pack

    def activate_pack(self, pack_id):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pack = self.pack(conn, pack_id)
            conn.execute("UPDATE library_state SET value=? WHERE key='current_pack'", (pack.id,))
            return pack

    def rename_pack(self, pack_id, value):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pack = self.pack(conn, pack_id)
            if pack.name != value.name:
                self.check_version(pack, value.expected_version)
                pack.name = value.name
                self.touch(pack)
                self.save(conn, "reference_packs", pack)
                conn.execute("UPDATE research_packs SET pending_name=0 WHERE pack_id=?", (pack.id,))
            return pack

    def delete_pack(self, pack_id, expected_version):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM deleted_packs WHERE id=?", (pack_id,)).fetchone():
                return
            pack = self.pack(conn, pack_id)
            self.check_version(pack, expected_version)
            conn.execute("INSERT INTO deleted_packs VALUES (?)", (pack_id,))
            conn.execute("UPDATE research_packs SET pending_name=0 WHERE pack_id=?", (pack_id,))
            current = conn.execute("SELECT value FROM library_state WHERE key='current_pack'").fetchone()[0]
            if current == pack_id:
                other = conn.execute(
                    "SELECT id FROM reference_packs WHERE NOT EXISTS "
                    "(SELECT 1 FROM deleted_packs WHERE id=reference_packs.id) ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if other:
                    next_id = other["id"]
                else:
                    replacement = ReferencePack(name="My Reference Pack")
                    self.save(conn, "reference_packs", replacement)
                    next_id = replacement.id
                conn.execute("UPDATE library_state SET value=? WHERE key='current_pack'", (next_id,))

    def add_reference(self, pack_id, value):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pack = self.pack(conn, pack_id)
            snapshot = self.snapshot(value, conn)
            saved, _ = self.register(conn, snapshot)
            if saved.id not in pack.references:
                self.check_version(pack, value.expected_version)
                if len(pack.ordered_ids) >= self.workflow.settings.max_references:
                    raise AppError(
                        "reference_limit", "The current pack has reached its reference limit.", 422
                    )
                pack.ordered_ids.append(saved.id)
                pack.references[saved.id] = snapshot
                self.touch(pack)
                self.save(conn, "reference_packs", pack)
            return pack

    def reorder_pack(self, pack_id, value):
        with self.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pack = self.pack(conn, pack_id)
            if value.ordered_ids != pack.ordered_ids:
                self.check_version(pack, value.expected_version)
                if not set(value.ordered_ids) <= set(pack.ordered_ids):
                    raise AppError("invalid_selection", "Pack order includes an unknown reference.", 422)
                pack.ordered_ids = list(value.ordered_ids)
                pack.references = {key: pack.references[key] for key in pack.ordered_ids}
                self.touch(pack)
                self.save(conn, "reference_packs", pack)
            return pack

    def remove_reference(self, pack_id, saved_id, expected_version):
        from backend.schemas.library import PackOrderInput

        with self.repository.connection() as conn:
            pack = self.pack(conn, pack_id)
        if saved_id not in pack.references:
            return pack
        return self.reorder_pack(
            pack_id,
            PackOrderInput(
                expected_version=expected_version,
                ordered_ids=[i for i in pack.ordered_ids if i != saved_id],
            ),
        )

    def previews(self, favorite_only=True):
        from backend.services.preview_images import clean_saved_preview

        with self.repository.connection() as conn:
            saved = self.all_saved(conn)
            if favorite_only:
                saved = [item for item in saved if item.favorite]
            result = {
                row["id"]: json.loads(row["payload"])
                for row in conn.execute("SELECT id,payload FROM library_previews")
            }
        keys = {item.id: self.preview_key(item.snapshots[item.favorite_snapshot]) for item in saved}
        previews = {item.id: preview for item in saved
                    if (preview := clean_saved_preview(result.get(keys[item.id]))) is not None}
        missing = [item for item in saved if item.id not in previews]
        if missing:
            from types import SimpleNamespace

            run = SimpleNamespace(
                results=[
                    item.snapshots[item.favorite_snapshot].reference.model_copy(update={"id": item.id})
                    for item in missing
                ]
            )
            fetched = self.thumbnails.for_run(run)
            previews.update(fetched)
            with self.repository.connection() as conn:
                for key, preview in fetched.items():
                    if preview.get("image"):
                        conn.execute(
                            "INSERT OR REPLACE INTO library_previews VALUES (?,?)",
                            (keys[key], json.dumps(preview)),
                        )
        return {item.id: previews.get(item.id, {}) for item in saved}

    def cached_previews(self, references):
        from backend.services.preview_images import clean_saved_preview

        with self.repository.connection() as conn:
            previews = {
                row["id"]: json.loads(row["payload"])
                for row in conn.execute("SELECT id,payload FROM library_previews")
            }
            return {
                key: preview
                for key, snapshot in references.items()
                if (preview := clean_saved_preview(previews.get(self.preview_key(snapshot)))) is not None
            }
