import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from backend.core.config import Settings
from backend.core.errors import AppError
from backend.library_routes import register_library_routes
from backend.providers.openai_provider import OpenAIProvider
from backend.repositories.sqlite import Repository
from backend.schemas.models import (
    ExportRecord,
    ExportRequest,
    ReferenceBrief,
    ResearchActionInput,
    ResearchSubmissionInput,
    ResearchTitleInput,
    SearchRequest,
    SearchRun,
    SearchStrategy,
    Selection,
    SelectionInput,
    StoredBrief,
)
from backend.schemas.source_registry import SourceCategoryMetadata
from backend.services.library import ReferenceLibrary
from backend.services.pptx_export import PPTX_MIME
from backend.services.presentation import PresentationStore
from backend.services.source_registry import load_source_registry
from backend.services.thumbnails import ThumbnailService
from backend.services.workflow import Workflow


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    repo = Repository(config.data_dir / "reference_finder.sqlite3")
    workflow = Workflow(config, repo)
    presentation = PresentationStore(repo)
    thumbnails = ThumbnailService(config, presentation)
    library = ReferenceLibrary(workflow, thumbnails)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo.initialize()
        presentation.initialize()
        library.initialize()
        yield

    app = FastAPI(title="Atomized Reference Finder", version="0.1.0", lifespan=lifespan)
    app.state.workflow = workflow
    app.state.thumbnails = thumbnails
    app.state.presentation = presentation
    app.state.library = library
    register_library_routes(app, library)

    @app.exception_handler(AppError)
    async def application_error(request: Request, error: AppError):
        return JSONResponse(
            status_code=error.status, content={"error": {"code": error.code, "message": error.message}}
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        messages = {
            404: "The requested endpoint does not exist.",
            405: "This HTTP method is not supported for this endpoint.",
        }
        return JSONResponse(
            status_code=error.status_code,
            headers=error.headers,
            content={
                "error": {
                    "code": "http_error",
                    "message": messages.get(error.status_code, "The HTTP request could not be processed."),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Do not echo rejected input (which may include secrets or a private brief).
        fields = [
            {"field": ".".join(str(part) for part in item["loc"]), "type": item["type"]}
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": f"Check the request fields and reference limit (1–{config.max_references}).",
                    "fields": fields,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        logging.getLogger(__name__).error("Unhandled request error: %s", type(error).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The request failed. Please retry or check the backend configuration.",
                }
            },
        )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "mode": config.app_mode,
            "max_references": config.max_references,
            "openai_configured": config.openai_configured,
            "live_search_ready": config.app_mode == "live" and config.search_configured,
            "live_search_verified": workflow.live_search_verified,
            "models": {"planning": config.openai_model, "search": config.openai_search_model},
            "search_provider": config.search_provider,
            "capabilities": {
                "mock_workflow": False,
                "live_strategy": config.openai_configured,
                "live_search": config.search_configured,
                "background_planning": True,
                "global_library": True,
            },
        }

    @app.get("/source-categories", response_model=list[SourceCategoryMetadata])
    def source_categories():
        return load_source_registry().metadata()

    @app.post("/providers/openai/check")
    def check_openai():
        # Explicit, user-triggered small model call; never run as part of health polling.
        brief = StoredBrief(concept="A short film explaining creative collaboration.", requested_count=1)
        strategy = OpenAIProvider(config).strategy(brief)
        return {
            "connected": True,
            "structured_output": True,
            "model": config.openai_model,
            "summary": strategy.summary,
            "live_search_verified": False,
        }

    @app.post("/briefs", response_model=StoredBrief, status_code=201)
    def create_brief(value: ReferenceBrief):
        return workflow.create_brief(value)

    @app.get("/briefs/{brief_id}", response_model=StoredBrief)
    def get_brief(brief_id: UUID):
        return workflow.brief(str(brief_id))

    @app.post("/briefs/{brief_id}/strategy", response_model=SearchStrategy)
    def create_strategy(brief_id: UUID):
        brief = workflow.brief(str(brief_id))
        strategy = workflow.provider.strategy(brief)
        if strategy.constraints and not brief.constraints:
            # Keep the original concept intact; expose extracted constraints to
            # search and feasibility scoring just like the optional API field.
            brief.constraints = strategy.constraints
            repo.put("brief", brief)
        return strategy

    @app.post("/search-runs", response_model=SearchRun, status_code=202)
    def submit_run(value: SearchRequest | ResearchSubmissionInput, background: BackgroundTasks):
        run, created = (
            workflow.submit_research(value) if isinstance(value, ResearchSubmissionInput)
            else workflow.submit(value)
        )
        if created:
            background.add_task(workflow.execute, run.id)
        return run

    @app.get("/search-runs")
    def recent_runs():
        return [
            {
                "id": run["id"],
                "mode": run["mode"],
                "status": run["status"],
                "summary": run["strategy"]["summary"][:120],
                "title": presentation.title(run),
                "created_at": run["created_at"],
            }
            for run in repo.recent_runs()
        ]

    @app.get("/search-runs/{run_id}", response_model=SearchRun)
    def get_run(run_id: UUID):
        return workflow.run(str(run_id))

    @app.post("/search-runs/{run_id}/actions", response_model=SearchRun, status_code=202)
    def submit_research_action(run_id: UUID, value: ResearchActionInput, background: BackgroundTasks):
        run, created = workflow.submit_action(str(run_id), value)
        if created:
            background.add_task(workflow.execute, run.id)
        return run

    @app.get("/search-runs/{run_id}/actions", response_model=list[SearchRun])
    def research_actions(run_id: UUID):
        return repo.research_actions(str(run_id))

    @app.patch("/search-runs/{run_id}/title")
    def rename_research(run_id: UUID, value: ResearchTitleInput):
        with workflow.edit_lock:
            presentation.rename(str(run_id), value.title)
        return {"id": str(run_id), "title": value.title}

    @app.delete("/search-runs/{run_id}")
    def delete_research(run_id: UUID):
        with workflow.edit_lock:
            repo.delete_research(str(run_id))
        return {"id": str(run_id), "deleted": True}

    @app.get("/search-runs/{run_id}/presentation")
    def run_presentation(run_id: UUID):
        run = workflow.run(str(run_id))
        previews = thumbnails.for_run(run) if run.mode == "live" else {}
        return {"title": presentation.title(run.model_dump(mode="json")), "previews": previews}

    @app.put("/search-runs/{run_id}/selection", response_model=Selection)
    def save_selection(run_id: UUID, value: SelectionInput):
        return workflow.save_selection(str(run_id), value)

    @app.post("/search-runs/{run_id}/exports/docx", response_model=ExportRecord, status_code=201)
    def export_docx(run_id: UUID, value: ExportRequest | None = None):
        return workflow.export(str(run_id), value.expected_selection_version if value else None)

    @app.post("/search-runs/{run_id}/exports/pptx", response_model=ExportRecord, status_code=201)
    def export_pptx(run_id: UUID, value: ExportRequest | None = None):
        run = workflow.run(str(run_id))
        return workflow.export(
            str(run_id),
            value.expected_selection_version if value else None,
            file_format="pptx",
            previews=thumbnails.for_export(run),
        )

    @app.get("/exports/{export_id}/download")
    def download_export(export_id: UUID):
        record = ExportRecord.model_validate(repo.get("export", str(export_id)))
        if record.pack_id is None:
            repo.get("run", record.run_id)
        path = config.data_dir / "exports" / record.filename
        if not path.is_file():
            raise AppError("export_missing", "Export file is missing; generate it again.", 404)
        return FileResponse(
            path,
            media_type=PPTX_MIME
            if record.format == "pptx"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"Atomized-Reference-Pack-{record.id[:8]}.{record.format}",
        )

    return app


app = create_app()
