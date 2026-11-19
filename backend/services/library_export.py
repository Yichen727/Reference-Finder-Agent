"""Render independent packs through the existing PowerPoint templates."""

from backend.core.errors import AppError
from backend.schemas.models import ExportRecord, SearchRun, Selection, StoredBrief
from backend.services.pptx_export import render_pptx


def export_library_pack(library, pack_id, expected_version):
    with library.repository.connection() as conn:
        pack = library.pack(conn, pack_id)
    library.check_version(pack, expected_version)
    if not pack.ordered_ids:
        raise AppError("empty_selection", "Add at least one reference before exporting.", 422)
    snapshots = [pack.references[key] for key in pack.ordered_ids]
    evidence = {item.id: item for snapshot in snapshots for item in snapshot.evidence}
    brief = StoredBrief(concept=pack.name, requested_count=len(pack.ordered_ids))
    strategy = snapshots[0].strategy.model_copy(update={"brief_id": brief.id, "research_title": pack.name})
    run = SearchRun(
        id=pack.id,
        brief_id=brief.id,
        strategy=strategy,
        mode="live",
        status="completed",
        results=[pack.references[key].reference.model_copy(update={"id": key}) for key in pack.ordered_ids],
        evidence=list(evidence.values()),
        selection=Selection(
            ordered_ids=pack.ordered_ids,
            expected_version=pack.version - 1,
            version=pack.version,
        ),
    )
    contexts = {
        key: {
            "run_id": snapshot.run_id,
            "reference_id": snapshot.reference_id,
            "batch_run_id": snapshot.batch_run_id,
            "brief": snapshot.brief,
            "warnings": snapshot.warnings,
            "pack_id": pack.id,
            "pack_name": pack.name,
        }
        for key, snapshot in pack.references.items()
    }
    record = ExportRecord(
        run_id=pack.id,
        pack_id=pack.id,
        selection_version=pack.version,
        filename="",
        format="pptx",
    )
    record.filename = f"{record.id}.pptx"
    folder = library.workflow.settings.data_dir / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    destination, temporary = folder / record.filename, folder / f".{record.id}.tmp"
    try:
        metadata = render_pptx(
            temporary,
            brief,
            run,
            library.cached_previews(pack.references),
            contexts=contexts,
        )
        record.template_version = metadata["template_version"]
        record.template_hashes = metadata["template_hashes"]
        with library.repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            library.check_version(library.pack(conn, pack.id), expected_version)
            temporary.replace(destination)
            conn.execute("INSERT INTO records VALUES ('export',?,?)", (record.id, record.model_dump_json()))
        return record
    except Exception as error:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        if isinstance(error, AppError):
            raise
        raise AppError(
            "export_failed", "Could not generate the PowerPoint. Your saved pack is kept.", 500
        ) from None
