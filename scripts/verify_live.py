"""Explicit opt-in live smoke check using a public sample brief and local credentials."""

import json
from uuid import uuid4

from backend.core.config import ROOT, Settings
from backend.repositories.sqlite import Repository
from backend.schemas.models import ReferenceBrief, SearchRequest, SelectionInput
from backend.services.workflow import Workflow


def main():
    settings = Settings()
    if not settings.search_configured:
        print("Configure OPENAI_API_KEY and the model settings in .env first.")
        return 1
    # An isolated database avoids interrupting jobs in the user's running workspace.
    settings.data_dir = ROOT / "data" / "live-verification" / str(uuid4())
    settings.app_mode = "live"
    repo = Repository(settings.data_dir / "reference_finder.sqlite3")
    repo.initialize()
    workflow = Workflow(settings, repo)
    brief = workflow.create_brief(
        ReferenceBrief(
            concept=(
                "A short, character-led sustainability campaign film using gentle humour "
                "to make a complex environmental issue tangible."
            ),
            purpose="Pitch research",
            audience="A broad adult audience; curiosity and constructive action",
            format="Short film",
            qualities="Human storytelling, unexpected humour, clear visual ideas",
            requested_count=4,
        )
    )
    print(
        f"Planning with {settings.openai_model}; searching with {settings.openai_search_model}.", flush=True
    )
    strategy = workflow.provider.strategy(brief)
    print(f"Structured strategy received: {len(strategy.strands)} strands.", flush=True)
    run, _ = workflow.submit(
        SearchRequest(brief_id=brief.id, strategy=strategy, idempotency_key=str(uuid4()))
    )
    workflow.execute(run.id)
    run = workflow.run(run.id)
    result = run.model_dump(mode="json")
    (settings.data_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"Status: {run.status}; references: {len(run.results)}; search calls: {run.usage.web_search_calls}.",
        flush=True,
    )
    print(
        f"Page checks: {run.usage.page_checks}; HTTP attempts: {run.usage.page_requests}; "
        f"readable pages: {run.usage.accessible_pages}.",
        flush=True,
    )
    for item in run.results:
        print(f"- {item.title} | {item.platform} | {item.source_url}", flush=True)
        if item.ranking:
            print(
                f"  Evidence: {item.verification_status}; fit: {item.ranking.score}; "
                f"merged source records: {item.merged_count}.",
                flush=True,
            )
    if not run.results:
        for warning in run.warnings:
            print(warning, flush=True)
        return 1
    workflow.save_selection(
        run.id, SelectionInput(ordered_ids=[item.id for item in run.results], expected_version=0)
    )
    record = workflow.export(run.id, file_format="pptx")
    print(f"Evidence and usage: {settings.data_dir / 'result.json'}", flush=True)
    print(f"PPTX: {settings.data_dir / 'exports' / record.filename}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
