import json
from pathlib import Path

from backend.providers.base import ProgressCallback
from backend.schemas.models import (
    Annotation,
    Evidence,
    QueryStrand,
    ReferenceItem,
    SearchOutcome,
    SearchStrategy,
    StoredBrief,
    new_id,
)


class MockProvider:
    def strategy(self, brief: StoredBrief) -> SearchStrategy:
        names = ["Tone & story", "Camera & edit", "Format & craft"]
        return SearchStrategy(
            brief_id=brief.id,
            summary="Mock research direction: " + brief.concept[:800],
            assumptions=["Demonstration only; no external search has run."],
            strands=[
                QueryStrand(
                    name=name,
                    intent="Explore creative dimension: " + name,
                    queries=[f"{brief.concept[:200]} {name}"],
                )
                for name in names
            ],
            preferred_sources=brief.preferred_sources,
            is_mock=True,
        )

    def search(self, brief: StoredBrief, strategy: SearchStrategy, progress: ProgressCallback | None = None):
        seeds = json.loads((Path(__file__).parent / "mock_references.json").read_text(encoding="utf-8"))
        results, evidence = [], []
        for index, seed in enumerate(seeds[: brief.requested_count]):
            platform = strategy.preferred_sources[index % len(strategy.preferred_sources)]
            strand = strategy.strands[index % len(strategy.strands)].name
            url = f"https://example.invalid/mock/reference-{index + 1:02}"
            item_evidence = Evidence(
                source_url=url,
                page_title=f"MOCK / {seed['title']}",
                excerpt="Synthetic fixture. No real work, creator, award or source has been verified.",
            )
            evidence.append(item_evidence)
            principle = seed["principle"]
            results.append(
                ReferenceItem(
                    id=new_id(),
                    title=f"MOCK / {seed['title']}",
                    platform=platform,
                    source_url=url,
                    tags=seed["tags"],
                    strand=strand,
                    evidence_ids=[item_evidence.id],
                    annotation=Annotation(
                        what_it_is="A fictional workflow example exploring: " + principle,
                        what_to_borrow=principle,
                        why_it_fits=(f"Illustrates the '{strand}' strand; reassess fit using real work."),
                        production_note="Illustrative only; no real production has been assessed.",
                        assumptions=["Mock data; not a recommendation of an existing work."],
                        rights_note="Synthetic text only. Placeholder URL; no image or video is included.",
                    ),
                )
            )
        return SearchOutcome(
            results=results,
            evidence=evidence,
            warnings=[
                "MOCK DEMONSTRATION: all examples and URLs are placeholders, not external search results."
            ],
        )
