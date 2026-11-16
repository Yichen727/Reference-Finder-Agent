import hashlib
import json
import threading

from backend.core.config import Settings
from backend.core.errors import AppError
from backend.providers.base import ReferenceProvider
from backend.providers.openai_provider import OpenAIProvider
from backend.repositories.sqlite import Repository
from backend.schemas.models import (
    ExportRecord,
    ReferenceBrief,
    SearchRequest,
    SearchRun,
    Selection,
    SelectionInput,
    StoredBrief,
    now,
)
from backend.services.docx_export import render_docx


class Workflow:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings, self.repository = settings, repository
        self.provider: ReferenceProvider = OpenAIProvider(settings)
        self.job_slot = threading.Semaphore(1)
        self.edit_lock = threading.Lock()
        self.live_search_verified = False

    def brief(self, brief_id: str) -> StoredBrief:
        return StoredBrief.model_validate(self.repository.get("brief", brief_id))

    def run(self, run_id: str) -> SearchRun:
        return SearchRun.model_validate(self.repository.get("run", run_id))

    def create_brief(self, value: ReferenceBrief) -> StoredBrief:
        if value.requested_count > self.settings.max_references:
            raise AppError(
                "reference_limit",
                f"This server allows at most {self.settings.max_references} references.",
                422,
            )
        brief = StoredBrief(**value.model_dump())
        self.repository.put("brief", brief)
        return brief

    def submit(self, request: SearchRequest) -> tuple[SearchRun, bool]:
        brief = self.brief(request.brief_id)
        if request.strategy.brief_id != brief.id:
            raise AppError("strategy_mismatch", "Strategy does not belong to this brief.", 422)
        if request.strategy.is_mock:
            raise AppError(
                "archived_strategy", "Start new research to generate a current search strategy.", 409
            )
        if not set(request.strategy.preferred_sources) <= set(brief.preferred_sources):
            raise AppError(
                "source_mismatch",
                "Strategy sources must be selected in the original brief.",
                422,
            )
        if not self.settings.search_configured:
            raise AppError("openai_not_configured", "Configure the OpenAI key and model settings first.", 503)
        run = SearchRun(brief_id=brief.id, strategy=request.strategy, mode=self.settings.app_mode)
        payload = request.model_dump(exclude={"idempotency_key"}, mode="json")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        result, created = self.repository.create_run(run, request.idempotency_key, fingerprint)
        return SearchRun.model_validate(result), created

    def execute(self, run_id: str):
        with self.job_slot:
            run = self.run(run_id)
            try:
                run.status, run.stage, run.updated_at = "running", "starting_external_search", now()
                self.repository.put("run", run)

                def progress(stage: str):
                    run.stage, run.updated_at = stage, now()
                    self.repository.put("run", run)

                outcome = self.provider.search(self.brief(run.brief_id), run.strategy, progress=progress)
                status = "partial" if outcome.partial else "completed"
                if not outcome.results:
                    status = "failed"
                # Validate complete provider output rather than trusting provider-side limits/citations.
                run = SearchRun.model_validate(
                    {
                        **run.model_dump(),
                        "results": outcome.results,
                        "evidence": outcome.evidence,
                        "warnings": outcome.warnings,
                        "usage": outcome.usage,
                        "coverage": outcome.coverage,
                        "page_checks": outcome.page_checks,
                        "error_code": outcome.error_code,
                        "status": status,
                        "stage": status,
                        "updated_at": now(),
                    }
                )
                if len(run.results) > min(
                    self.settings.max_references, self.brief(run.brief_id).requested_count
                ):
                    raise AppError("provider_limit", "Provider returned more references than allowed.", 502)
                if run.mode == "live" and run.results and run.usage.web_search_calls:
                    self.live_search_verified = True
            except AppError as error:
                run.status, run.stage, run.error_code = "failed", "failed", error.code
                run.warnings.append(error.message)
                run.results, run.evidence = [], []
            except Exception:
                run.status, run.stage, run.error_code = "failed", "failed", "run_failed"
                run.warnings.append("The run failed. Please try again or inspect the local configuration.")
                run.results, run.evidence = [], []
            run.updated_at = now()
            self.repository.put("run", run)

    def save_selection(self, run_id: str, value: SelectionInput) -> Selection:
        with self.edit_lock:
            run = self.run(run_id)
            if run.mode != "live":
                raise AppError("archived_run", "Archived demonstration runs are read-only.", 409)
            if run.status not in {"completed", "partial"}:
                raise AppError("run_not_ready", "Wait for the run to finish before saving a selection.", 409)
            version = run.selection.version if run.selection else 0
            if value.expected_version != version:
                raise AppError(
                    "selection_conflict",
                    "The saved selection changed. Reload the run before saving again.",
                    409,
                )
            valid_ids = {item.id for item in run.results}
            if not set(value.ordered_ids) <= valid_ids:
                raise AppError("invalid_selection", "Selection contains references outside this run.", 422)
            if not set(value.rejected_ids) <= valid_ids:
                raise AppError("invalid_selection", "Rejected references must belong to this run.", 422)
            if not set(value.annotation_overrides) <= set(value.ordered_ids):
                raise AppError(
                    "invalid_override", "Only selected references may have annotation overrides.", 422
                )
            if len(value.ordered_ids) > self.settings.max_references:
                raise AppError("reference_limit", "Selection exceeds the server reference limit.", 422)
            selection = Selection(**value.model_dump(), version=version + 1)
            run.selection, run.updated_at = selection, now()
            self.repository.put("run", run)
            return selection

    def export(self, run_id: str, expected_version: int | None = None) -> ExportRecord:
        run = self.run(run_id)
        if run.mode != "live":
            raise AppError("archived_run", "Archived demonstration runs cannot be exported.", 409)
        if not run.selection or not run.selection.ordered_ids:
            raise AppError("empty_selection", "Save at least one selected reference before exporting.", 422)
        if expected_version is not None and run.selection.version != expected_version:
            raise AppError(
                "selection_conflict", "The saved pack changed before export. Reload and try again.", 409
            )
        record = ExportRecord(run_id=run.id, selection_version=run.selection.version, filename="")
        record.filename = f"{record.id}.docx"
        folder = self.settings.data_dir / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            render_docx(folder / record.filename, self.brief(run.brief_id), run)
            self.repository.put("export", record)
        except Exception:
            raise AppError(
                "export_failed", "Could not generate the document. Check disk space and retry.", 500
            ) from None
        return record
