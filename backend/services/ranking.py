"""Evidence-aware ranking, conservative work identity and diverse final selection."""

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlsplit

from backend.core.limits import MAX_REFERENCES
from backend.schemas.models import RankingDetails, ReferenceItem, StoredBrief

NOISE = {"official", "film", "video", "case", "study", "watch", "vimeo", "youtube", "www", "com"}
WEIGHTS = {"relevance": 30, "specificity": 25, "craft": 15, "source": 20, "feasibility": 10}
SOURCE_RATINGS = {
    "Studio portfolios": 5.0,
    "Production companies": 5.0,
    "Award archives": 4.5,
    "Vimeo": 4.0,
    "YouTube": 4.0,
    "Instagram": 3.0,
    "Pinterest": 2.5,
}


def words(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def media_identity(url: str) -> str | None:
    parsed = urlsplit(str(url))
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.strip("/")
    if host == "youtu.be" and path:
        return "youtube:" + path.split("/")[0]
    if host in {"youtube.com", "m.youtube.com", "youtube-nocookie.com"}:
        video = parse_qs(parsed.query).get("v", [""])[0]
        if not video:
            match = re.match(r"(?:embed|shorts|live)/([^/]+)", path)
            video = match.group(1) if match else ""
        return "youtube:" + video if video else None
    if host in {"vimeo.com", "player.vimeo.com"}:
        match = re.search(r"(?:^|/)(\d{5,})(?:/|$)", path)
        return "vimeo:" + match.group(1) if match else None
    if host == "instagram.com":
        match = re.match(r"(?:p|reel|reels)/([^/]+)", path)
        return "instagram:" + match.group(1) if match else None
    if host == "pinterest.com" or host.endswith(".pinterest.com") or host == "pinterest.co.uk":
        match = re.match(r"pin/(\d+)", path)
        return "pinterest:" + match.group(1) if match else None
    return None


def title_key(item: ReferenceItem) -> str:
    creator = set(words(item.creator).split())
    parts = re.split(r"\s+[|\u2013\u2014]\s+", item.title)
    parts = [part for part in parts if not set(words(part).split()) <= creator | NOISE]
    value = max(parts, key=lambda part: len(words(part).split()), default=item.title)
    tokens = [token for token in words(value).split() if token not in NOISE]
    return " ".join(tokens)


def identities(item: ReferenceItem) -> set[str]:
    urls = [str(item.source_url)] + [str(url) for url in item.alternate_urls]
    if item.page_check:
        urls += [str(url) for url in [item.page_check.final_url, item.page_check.canonical_url] if url]
        if item.page_check.work_page:
            urls += [str(url) for url in item.page_check.media_urls]
    return {identity for url in urls if (identity := media_identity(url))}


def same_work(left: ReferenceItem, right: ReferenceItem) -> bool:
    if str(left.source_url) == str(right.source_url):
        return True
    shared = identities(left) & identities(right)
    if shared and (
        media_identity(str(left.source_url)) in shared or media_identity(str(right.source_url)) in shared
    ):
        return True
    if left.page_check and right.page_check:
        if left.page_check.final_url and left.page_check.final_url == right.page_check.final_url:
            return True
        a, b = left.page_check.canonical_url, right.page_check.canonical_url
        if (
            a
            and b
            and str(a) == str(b)
            and left.page_check.work_page
            and right.page_check.work_page
            and title_key(left) == title_key(right)
        ):
            return True
    a, b = title_key(left), title_key(right)
    # Generic titles and unrelated creators should not collapse into a single work.
    if len(a.split()) < 2 or len(b.split()) < 2:
        return False
    left_years, right_years = set(re.findall(r"\b20\d{2}\b", a)), set(re.findall(r"\b20\d{2}\b", b))
    if left_years and right_years and left_years != right_years:
        return False
    creators_match = bool(left.creator and right.creator and words(left.creator) == words(right.creator))
    if left.creator and right.creator and not creators_match:
        return False
    if a == b:
        return True
    overlap = len(set(a.split()) & set(b.split())) / max(len(set(a.split()) | set(b.split())), 1)
    return (creators_match or bool(shared)) and overlap >= 0.8 and SequenceMatcher(None, a, b).ratio() >= 0.9


def representative_priority(item: ReferenceItem):
    originals = original_source(item)
    return (item.verification_status == "supported", originals, item.ranking.score if item.ranking else 0)


def original_source(item: ReferenceItem) -> bool:
    return item.platform in {"Studio portfolios", "Production companies"} or (
        item.source_category == "Studios & Production Companies" and item.platform not in SOURCE_RATINGS
    )


def source_rating(item: ReferenceItem) -> float:
    if item.platform in SOURCE_RATINGS:
        return SOURCE_RATINGS[item.platform]
    if original_source(item):
        return SOURCE_RATINGS["Studio portfolios"]
    if item.source_category == "Awards & Winning Work":
        return SOURCE_RATINGS["Award archives"]
    # A newly supported publisher is not automatically an original creator.
    return 3.5


def merge_works(items: list[ReferenceItem]) -> list[ReferenceItem]:
    groups: list[list[ReferenceItem]] = []
    for item in items:
        matching = [group for group in groups if any(same_work(item, other) for other in group)]
        if not matching:
            groups.append([item])
        else:
            target = matching[0]
            target.append(item)
            for group in matching[1:]:
                target.extend(group)
                groups.remove(group)
    merged = []
    for group in groups:
        best = max(group, key=representative_priority).model_copy(deep=True)
        ordered = [best] + [item for item in group if item.id != best.id]
        links = list(
            dict.fromkeys(url for item in ordered for url in [item.source_url] + item.alternate_urls)
        )
        best.alternate_urls = [url for url in links if str(url) != str(best.source_url)][:20]
        best.evidence_ids = list(dict.fromkeys(item_id for item in ordered for item_id in item.evidence_ids))[
            :20
        ]
        best.merged_count = sum(item.merged_count for item in group)
        if len(group) > 1 and best.ranking:
            best.ranking.reasons.append(
                f"Merged {len(group)} source records for this work; alternate links are preserved."
            )
            best.ranking.reasons = best.ranking.reasons[:10]
        merged.append(best)
    return merged


def score_reference(item: ReferenceItem, brief: StoredBrief, valid_evidence: set[str]) -> bool:
    assessment = item.assessment
    if assessment and not set(assessment.evidence_ids) <= valid_evidence:
        return False
    if assessment and (assessment.relevance < 2 or assessment.specificity < 1):
        return False
    if not all([item.annotation.what_it_is, item.annotation.what_to_borrow, item.annotation.why_it_fits]):
        return False
    if assessment:
        components = {
            key: float(getattr(assessment, key))
            for key in ["relevance", "specificity", "craft", "feasibility"]
        }
        reasons = list(assessment.reasons)
        role = assessment.role
    else:
        # A transparent fallback, not a claim that craft was independently judged.
        terms = set(words(brief.concept + " " + brief.qualities).split()) - {
            "a",
            "the",
            "and",
            "with",
            "of",
            "to",
        }
        text = set(
            words(item.title + " " + item.annotation.what_it_is + " " + item.annotation.why_it_fits).split()
        )
        relevance = min(4.0, 2.0 + 2.0 * len(terms & text) / max(len(terms), 1))
        components = {"relevance": relevance, "specificity": 2.0, "craft": 1.0, "feasibility": 2.5}
        reasons = [
            "Baseline fit uses the supplied Brief and available evidence; creative scoring was unavailable."
        ]
        role = "close_match"
    checked = item.verification_status == "supported"
    components["source"] = source_rating(item) if checked else 1.5
    if not checked:
        components["craft"] = min(components["craft"], 2.0)
    reasons.append(
        "Readable work-page identity was checked."
        if checked
        else "Only search evidence is available; page identity needs review."
    )
    if checked and item.platform in {"Instagram", "Pinterest"}:
        reasons.append(
            "Public social/repost sources have lower source authority than original project pages."
        )
    if not brief.constraints:
        components["feasibility"] = min(components["feasibility"], 2.5)
        reasons.append("Production constraints were not supplied; feasibility remains provisional.")
    score = round(sum(WEIGHTS[key] * value / 5 for key, value in components.items()), 2)
    item.ranking = RankingDetails(score=score, components=components, reasons=reasons[:10], role=role)
    return True


def diverse_shortlist(items: list[ReferenceItem], limit: int) -> list[ReferenceItem]:
    pool = merge_works(items)
    selected = []
    while pool and len(selected) < min(limit, MAX_REFERENCES):

        def value(item):
            score = item.ranking.score
            if not selected:
                return score
            creator_count = sum(
                bool(item.creator) and words(other.creator) == words(item.creator) for other in selected
            )
            host = urlsplit(str(item.source_url)).hostname
            host_count = sum(urlsplit(str(other.source_url)).hostname == host for other in selected)
            tag_set = set(tag.lower() for tag in item.tags)
            overlap = max(
                (
                    len(tag_set & set(tag.lower() for tag in other.tags))
                    / max(len(tag_set | set(tag.lower() for tag in other.tags)), 1)
                    for other in selected
                ),
                default=0,
            )
            novelty = 4 if item.strand not in {other.strand for other in selected} else 0
            novelty += 2 if item.ranking.role not in {other.ranking.role for other in selected} else 0
            return score + novelty - min(creator_count * 5, 15) - min(host_count * 3, 9) - overlap * 6

        best = max(pool, key=lambda item: (value(item), item.ranking.score))
        best.ranking.selection_note = (
            "Strongest initial evidence-weighted match."
            if not selected
            else "Balances evidence-weighted fit with variety in strands, roles, creators and sources."
        )
        selected.append(best)
        pool.remove(best)
    return selected
