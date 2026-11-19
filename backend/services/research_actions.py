"""Merge completed additional research without replacing an existing curation."""

from backend.core.errors import AppError
from backend.schemas.models import SearchRun, SourceCoverage, UsageStats, now
from backend.services.ranking import same_work
from config import current_source_name


def append_batch(parent: SearchRun, batch: SearchRun, requested_count: int):
    if batch.id in parent.appended_batch_ids:
        return
    existing = list(parent.results)
    new = []
    used_ids = {item.id for item in existing}
    for item in batch.results:
        if item.id not in used_ids and not any(same_work(item, old) for old in existing + new):
            new.append(item)
            used_ids.add(item.id)
    if not new:
        raise AppError(
            "no_additional_references",
            "No new references remained after removing existing works. Your original results are kept.",
            422,
        )
    batch.results = new
    batch.added_reference_ids = [item.id for item in new]
    if len(new) < requested_count:
        batch.status = batch.stage = "partial"
        batch.warnings.append(f"Added {len(new)} new references; requested {requested_count}.")
    parent.results.extend(new)
    known_evidence = {item.id for item in parent.evidence}
    parent.evidence.extend(item for item in batch.evidence if item.id not in known_evidence)
    parent.page_checks.extend(batch.page_checks)
    values = parent.usage.model_dump()
    for key, value in batch.usage.model_dump().items():
        values[key] = list(dict.fromkeys(values[key] + value)) if key == "models" else values[key] + value
    parent.usage = UsageStats(**values)
    coverage = {}
    for item in parent.coverage + batch.coverage:
        category = current_source_name(item.category)
        current = coverage.setdefault(category, SourceCoverage(category=category, note=item.note))
        current.queries_attempted += item.queries_attempted
    for item in coverage.values():
        item.references_returned = sum(
            current_source_name(ref.source_category) == item.category for ref in parent.results
        )
    parent.coverage = list(coverage.values())
    parent.warnings = list(dict.fromkeys(parent.warnings + batch.warnings))
    parent.appended_batch_ids.append(batch.id)
    parent.updated_at = now()
    # Revalidate the merged citation graph before the transaction is committed.
    SearchRun.model_validate(parent.model_dump())
