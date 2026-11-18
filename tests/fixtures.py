"""Deterministic provider used only by tests; not an application runtime mode."""

from backend.schemas.models import (
    Annotation,
    Evidence,
    QueryStrand,
    ReferenceItem,
    SearchOutcome,
    SearchStrategy,
    new_id,
)


class FixtureProvider:
    def strategy(self, brief):
        return SearchStrategy(
            brief_id=brief.id,
            summary="A test research direction",
            research_title="Human Side of AI",
            assumptions=[],
            preferred_sources=brief.preferred_sources,
            strands=[QueryStrand(name="Story", intent="Find human stories", queries=["human story film"])],
        )

    def search(self, brief, strategy, progress=None):
        results, evidence = [], []
        for index in range(brief.requested_count):
            url = f"https://studio.test/work/{new_id()}"
            source = Evidence(
                source_url=url,
                page_title=f"Test Reference {index + 1}",
                excerpt="Test-only source description.",
                evidence_type="search_summary",
            )
            evidence.append(source)
            results.append(
                ReferenceItem(
                    id=new_id(),
                    title=f"Test Reference {index + 1}",
                    platform=brief.preferred_sources[0],
                    source_url=url,
                    tags=["story"],
                    strand="Story",
                    evidence_ids=[source.id],
                    annotation=Annotation(
                        what_it_is="A test-only film description.",
                        what_to_borrow="A specific creative principle.",
                        why_it_fits="A connection to the supplied brief.",
                        production_note="Needs producer review.",
                    ),
                )
            )
        return SearchOutcome(results=results, evidence=evidence)
