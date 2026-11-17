import pytest
from pydantic import HttpUrl

from backend.providers.discovery import supported_description
from backend.schemas.models import Annotation, CreativeAssessment, PageCheck, ReferenceItem, StoredBrief
from backend.services.ranking import (
    diverse_shortlist,
    media_identity,
    merge_works,
    same_work,
    score_reference,
)


def test_work_description_cannot_add_unsourced_numbers_or_awards():
    source = "Shared Futures is a 30-second environmental film by Example Studio."
    assert supported_description("A 30-second environmental film.", source)
    assert not supported_description("It attracted 1000000 viewers.", source)
    assert not supported_description("A Cannes award-winning environmental film.", source)


def reference(
    title="Shared Futures",
    url="https://studio.test/work/shared-futures",
    creator="Example Studio",
    *,
    checked=True,
    score=4,
    strand="Tone",
    role="close_match",
):
    return ReferenceItem(
        id=url,
        title=title,
        creator=creator,
        source_url=url,
        platform="Studio portfolios",
        tags=["human story"],
        strand=strand,
        evidence_ids=[url],
        verification_status="supported" if checked else "unverified",
        annotation=Annotation(
            what_it_is="A warm character-led environmental film.",
            what_to_borrow="Build the argument around a human perspective.",
            why_it_fits="Connects a complex environmental issue to this audience.",
            production_note="Casting and access need producer review.",
        ),
        assessment=CreativeAssessment(
            relevance=score,
            specificity=score,
            craft=score,
            feasibility=score,
            role=role,
            reasons=["Human storytelling supports this route."],
            evidence_ids=[url],
        ),
    )


def score(item):
    assert score_reference(
        item,
        StoredBrief(concept="A human environmental film", constraints="A small shoot"),
        set(item.evidence_ids),
    )
    return item


@pytest.mark.parametrize(
    "first, second",
    [
        ("https://youtu.be/abc123xyz00", "https://www.youtube.com/watch?v=abc123xyz00"),
        ("https://youtube.com/shorts/abc123xyz00", "https://youtube.com/embed/abc123xyz00"),
        ("https://vimeo.com/123456789/privatehash", "https://player.vimeo.com/video/123456789?h=privatehash"),
        ("https://instagram.com/reel/AbCdEf", "https://www.instagram.com/p/AbCdEf"),
    ],
)
def test_media_url_variants_share_work_identity(first, second):
    assert media_identity(first) == media_identity(second)
    assert same_work(reference(url=first), reference(url=second))


def test_verified_project_and_embedded_video_merge_with_sources_preserved():
    project = score(reference())
    project.page_check = PageCheck(
        original_url=str(project.source_url),
        final_url=project.source_url,
        status="accessible",
        work_page=True,
        media_urls=["https://www.youtube.com/embed/abc123xyz00"],
    )
    video = score(
        reference(title="Shared Futures official film", url="https://youtu.be/abc123xyz00", checked=False)
    )
    video.platform = "YouTube"
    merged = merge_works([video, project])
    assert len(merged) == 1 and merged[0].source_url == project.source_url
    assert merged[0].alternate_urls == [video.source_url]
    assert set(merged[0].evidence_ids) == set(project.evidence_ids + video.evidence_ids)
    assert merged[0].merged_count == 2


def test_distinct_works_are_not_merged_by_a_shared_studio_embed():
    first = reference(title="A Bright Tomorrow", url="https://studio.test/work/a")
    second = reference(title="The Long Journey", url="https://studio.test/work/b")
    for item in [first, second]:
        item.page_check = PageCheck(
            original_url=str(item.source_url), work_page=True, media_urls=["https://vimeo.com/123456789"]
        )
    assert not same_work(first, second)


@pytest.mark.parametrize(
    "first, second",
    [
        (reference(creator="Studio A"), reference(creator="Studio B", url="https://other.test/work/a")),
        (
            reference(title="Shared Futures 2025"),
            reference(title="Shared Futures 2026", url="https://other.test/work/a"),
        ),
        (reference(title="Brand Film"), reference(title="Brand Film", url="https://other.test/work/a")),
    ],
)
def test_generic_names_conflicting_creators_and_distinct_years_are_not_collapsed(first, second):
    assert not same_work(first, second)


def test_readable_work_evidence_increases_confidence_without_an_awards_bonus():
    verified, search_only = score(reference()), score(reference(checked=False))
    assert verified.ranking.score > search_only.ranking.score
    assert search_only.ranking.components["craft"] <= 2
    assert verified.ranking.components["source"] == 5


def test_repost_platform_does_not_gain_original_source_authority_from_http_success():
    original = score(reference())
    repost = reference(url="https://pinterest.com/pin/1234567")
    repost.platform = "Pinterest"
    score(repost)
    assert repost.ranking.components["source"] < original.ranking.components["source"]
    assert repost.ranking.score < original.ranking.score


def test_irrelevant_or_unanchored_assessments_fail_quality_gate():
    item = reference(score=1)
    assert not score_reference(item, StoredBrief(concept="A human film"), set(item.evidence_ids))
    item = reference()
    item.assessment.evidence_ids = ["invented-evidence"]
    assert not score_reference(item, StoredBrief(concept="A human film"), set(item.evidence_ids))


def test_diversity_can_add_a_complementary_execution_over_another_near_clone():
    first = score(reference(title="A Shared Future", url="https://studio.test/work/a"))
    clone = score(reference(title="Another Human Story", url="https://studio.test/work/b"))
    different = score(
        reference(
            title="A Tactile World",
            url="https://different.test/work/c",
            creator="Other Studio",
            strand="Execution",
            role="execution",
        )
    )
    different.tags = ["practical animation"]
    result = diverse_shortlist([first, clone, different], 2)
    assert [item.id for item in result] == [first.id, different.id]
    assert all(item.ranking.reasons and item.ranking.selection_note for item in result)


def test_final_shortlist_never_exceeds_twenty():
    items = [
        score(reference(title=f"Distinct Work {index}", url=f"https://studio.test/work/{index}"))
        for index in range(25)
    ]
    assert len(diverse_shortlist(items, 100)) == 20


def test_private_vimeo_hash_remains_in_alternate_link():
    first = score(reference(url="https://vimeo.com/123456789/privatehash"))
    second = score(reference(url="https://player.vimeo.com/video/123456789?h=privatehash"))
    result = merge_works([first, second])
    assert len(result) == 1
    assert result[0].alternate_urls == [HttpUrl("https://player.vimeo.com/video/123456789?h=privatehash")]
