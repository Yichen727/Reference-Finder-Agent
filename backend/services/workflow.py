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
    ResearchActionInput,
    SearchRequest,
    SearchRun,
    Selection,
    SelectionInput,
    StoredBrief,
    normalize_sources,
    now,
)
from backend.services.docx_export import render_docx
from backend.services.pptx_export import render_pptx
from backend.services.research_actions import append_batch


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
        brief = StoredBrief(
            **{**value.model_dump(), "preferred_sources": normalize_sources(value.preferred_sources)}
        )
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
        if not set(normalize_sources(request.strategy.preferred_sources)) <= set(
            normalize_sources(brief.preferred_sources)
        ):
            raise AppError(
                "source_mismatch",
                "Strategy sources must be selected in the original brief.",
                422,
            )
        if not self.settings.search_configured:
            raise AppError("openai_not_configured", "Configure the OpenAI key and model settings first.", 503)
        strategy = request.strategy.model_copy(
            update={
                "preferred_sources": normalize_sources(request.strategy.preferred_sources),
                "source_priority": normalize_sources(request.strategy.source_priority),
            }
        )
        run = SearchRun(brief_id=brief.id, strategy=strategy, mode=self.settings.app_mode)
        payload = request.model_dump(exclude={"idempotency_key"}, mode="json")
        if not request.strategy.source_priority:
            # Keep fingerprints compatible with submissions saved before this field existed.
            payload["strategy"].pop("source_priority", None)
        if not request.strategy.research_title:
            payload["strategy"].pop("research_title", None)
        if not request.strategy.constraints:
            payload["strategy"].pop("constraints", None)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        result, created = self.repository.create_run(run, request.idempotency_key, fingerprint)
        return SearchRun.model_validate(result), created

    def submit_action(self, parent_id: str, request: ResearchActionInput) -> tuple[SearchRun, bool]:
        parent = self.run(parent_id)
        if request.brief.requested_count > self.settings.max_references:
            raise AppError("reference_limit", "Reference count exceeds the server limit.", 422)
        if not self.settings.search_configured:
            raise AppError("openai_not_configured", "Configure the OpenAI key and model settings first.", 503)
        brief = StoredBrief(
            **{**request.brief.model_dump(),
               "preferred_sources": normalize_sources(request.brief.preferred_sources)}
        )
        # A queued task carries the previous direction until its own planning step completes.
        # It cannot search with this placeholder: execute() must finish planning first.
        strategy = parent.strategy.model_copy(update={
            "brief_id": brief.id, "preferred_sources": brief.preferred_sources,
            "source_priority": [], "constraints": "", "research_title": "",
        })
        run = SearchRun(
            brief_id=brief.id, strategy=strategy, mode="live", operation=request.action,
            parent_run_id=parent.id, planning_pending=True,
        )
        payload = {"parent_run_id": parent_id, **request.model_dump(exclude={"idempotency_key"}, mode="json")}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        result, created = self.repository.create_run(run, request.idempotency_key, fingerprint, brief=brief)
        return SearchRun.model_validate(result), created

    def execute(self, run_id: str):
        with self.job_slot:
            run = self.run(run_id)
            if run.status not in {"queued", "running"}:
                return
            try:
                if run.planning_pending:
                    run.status, run.stage, run.updated_at = "running", "analysing_brief", now()
                    self.repository.put("run", run)
                    brief = self.brief(run.brief_id)
                    strategy = self.provider.strategy(brief)
                    sources_match = set(normalize_sources(strategy.preferred_sources)) <= set(
                        brief.preferred_sources)
                    if strategy.brief_id != brief.id or not sources_match:
                        raise AppError("strategy_mismatch", "Strategy does not match the edited brief.", 502)
                    run.strategy, run.planning_pending = strategy, False
                    if strategy.constraints and not brief.constraints:
                        brief.constraints = strategy.constraints
                        self.repository.put("brief", brief)
                run.status, run.stage, run.updated_at = "running", "starting_external_search", now()
                self.repository.put("run", run)

                def progress(stage: str):
                    run.stage, run.updated_at = stage, now()
                    self.repository.put("run", run)

                search_options = {"progress": progress}
                if run.operation == "add":
                    search_options["excluded_works"] = self.run(run.parent_run_id).results
                outcome = self.provider.search(self.brief(run.brief_id), run.strategy, **search_options)
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
                if run.operation == "add" and run.results:
                    with self.edit_lock, self.repository.edit_run(run.parent_run_id) as (parent, conn):
                        append_batch(parent, run, self.brief(run.brief_id).requested_count)
                        conn.execute(
                            "UPDATE records SET payload=? WHERE kind='run' AND id=?",
                            (run.model_dump_json(), run.id),
                        )
                    return
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
        with self.edit_lock, self.repository.edit_run(run_id) as (run, _):
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
            return selection

    def export(
        self, run_id: str, expected_version: int | None = None, *, file_format="docx", previews=None
    ) -> ExportRecord:
        run = self.run(run_id)
        if run.mode != "live":
            raise AppError("archived_run", "Archived demonstration runs cannot be exported.", 409)
        if not run.selection or not run.selection.ordered_ids:
            raise AppError("empty_selection", "Save at least one selected reference before exporting.", 422)
        if expected_version is not None and run.selection.version != expected_version:
            raise AppError(
                "selection_conflict", "The saved pack changed before export. Reload and try again.", 409
            )
        if file_format not in {"docx", "pptx"}:
            raise AppError("export_format", "This export format is unsupported.", 422)
        record = ExportRecord(
            run_id=run.id, selection_version=run.selection.version, filename="", format=file_format
        )
        record.filename = f"{record.id}.{file_format}"
        folder = self.settings.data_dir / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / record.filename
        temporary = folder / f".{record.id}.tmp"
        try:
            if file_format == "pptx":
                metadata = render_pptx(temporary, self.brief(run.brief_id), run, previews)
                record.template_version = metadata["template_version"]
                record.template_hashes = metadata["template_hashes"]
            else:
                render_docx(temporary, self.brief(run.brief_id), run)
            temporary.replace(destination)
            self.repository.put("export", record)
        except AppError:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise AppError(
                "export_failed", "Could not generate the document. Check disk space and retry.", 500
            ) from None
        return record
