import json
import re
import time
from dataclasses import dataclass

import openai

from backend.core.config import ROOT, Settings
from backend.core.errors import AppError
from backend.core.limits import MAX_REFERENCES
from backend.providers.base import ProgressCallback
from backend.providers.openai_errors import safe_openai_errors
from backend.schemas.models import (
    DiscoveryDraft,
    Evidence,
    PageCheck,
    ReferenceItem,
    SearchOutcome,
    SearchStrategy,
    SourceCoverage,
    StoredBrief,
    UsageStats,
    new_id,
    normalize_sources,
)
from backend.services.page_verification import PageVerifier
from backend.services.ranking import diverse_shortlist, merge_works, score_reference
from backend.services.source_policy import SearchJob, SourcePolicy, canonical_url, publishing_platform


class RequestBudget:
    """Bound actual application-level requests, including explicit retries."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.started = time.monotonic()
        self.usage = UsageStats()

    def call(self, operation, model: str, *, reserve: int = 0):
        for attempt in range(self.settings.search_retry_count + 1):
            remaining = self.settings.search_run_timeout_seconds - (time.monotonic() - self.started)
            if remaining <= 0 or self.usage.requests_attempted >= self.settings.search_max_requests - reserve:
                raise AppError(
                    "search_budget_exhausted", "The search time or request budget was reached.", 504
                )
            self.usage.requests_attempted += 1
            if model not in self.usage.models:
                self.usage.models.append(model)
            try:
                with safe_openai_errors():
                    response = operation(timeout=min(remaining, self.settings.search_timeout_seconds))
                self.usage.requests_succeeded += 1
                usage = getattr(response, "usage", None)
                self.usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
                self.usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
                self.usage.web_search_calls += sum(
                    getattr(item, "type", "") == "web_search_call"
                    and getattr(item, "status", "") == "completed"
                    for item in getattr(response, "output", [])
                )
                return response
            except AppError as error:
                retryable = error.code in {
                    "openai_timeout",
                    "openai_connection",
                    "openai_rate_limit",
                    "openai_unavailable",
                }
                if not retryable or attempt >= self.settings.search_retry_count:
                    raise
                time.sleep(0.5 * (attempt + 1))
        raise AssertionError("Unreachable request budget state")

    def finish(self) -> UsageStats:
        self.usage.elapsed_seconds = round(time.monotonic() - self.started, 2)
        return self.usage


@dataclass
class SourceNote:
    evidence: Evidence
    categories: list[str]
    strand: str
    page: PageCheck | None = None
    page_evidence: Evidence | None = None


def citation_excerpt(text: str, index: int) -> str:
    if not isinstance(index, int) or index < 0 or index > len(text):
        return ""
    start = text.rfind("\n\n", 0, index) + 2
    start = max(0, start if start > 1 else 0)
    end = text.find("\n\n", index)
    section = text[start : end if end >= 0 else len(text)].strip()
    # Retain nearby context without persisting an unbounded search response.
    if len(section) > 1800:
        relative = index - start
        section = section[max(0, relative - 1300) : relative + 500]
    return section


def source_notes(response, job: SearchJob) -> list[SourceNote]:
    data = response.model_dump(mode="json")
    calls = [
        item
        for item in data.get("output", [])
        if item.get("type") == "web_search_call" and item.get("status") == "completed"
    ]
    if not calls:
        raise AppError(
            "search_not_executed", "The response did not contain a completed Web Search call.", 502
        )
    catalogue = {}
    for call in calls:
        for source in (call.get("action") or {}).get("sources", []) or []:
            url = canonical_url(source.get("url", ""))
            if url:
                catalogue[url] = source
    notes: dict[str, SourceNote] = {}
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text", "")
            for annotation in content.get("annotations", []):
                if annotation.get("type") != "url_citation":
                    continue
                url = canonical_url(annotation.get("url", ""))
                if not url or (catalogue and url not in catalogue):
                    continue
                excerpt = citation_excerpt(text, annotation.get("start_index", -1))
                note = SourceNote(
                    evidence=Evidence(
                        source_url=url,
                        page_title=(
                            annotation.get("title")
                            or catalogue.get(url, {}).get("title")
                            or "Untitled source"
                        )[:6000],
                        excerpt=excerpt or "Consulted source; no adjacent work description was available.",
                        access_status="unverified",
                        evidence_type="search_summary" if excerpt else "source_listing",
                    ),
                    categories=job.categories,
                    strand=job.strand,
                )
                if url in notes and len(notes[url].evidence.excerpt) > len(note.evidence.excerpt):
                    continue
                notes[url] = note
    # Consulted-only URLs are kept as provenance, not fabricated work descriptions.
    for url, source in catalogue.items():
        if url not in notes:
            notes[url] = SourceNote(
                Evidence(
                    source_url=url,
                    page_title=(source.get("title") or "Untitled source")[:6000],
                    excerpt="Consulted by Web Search; no cited work description was attached.",
                    access_status="unverified",
                    evidence_type="source_listing",
                ),
                job.categories,
                job.strand,
            )
    return list(notes.values())


def normalized_words(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def supported_description(description: str, source: str) -> bool:
    # Reject new numeric facts or award claims absent from the supplied source text.
    def numbers(text):
        return set(re.findall(r"\b\d+(?:[.,]\d+)*%?", text.replace(",", "")))

    if not numbers(description) <= numbers(source):
        return False
    award_terms = {"cannes", "bafta", "grierson", "d&ad", "lovie", "shorty", "oscar"}
    if any(term in description.casefold() and term not in source.casefold() for term in award_terms):
        return False
    if re.search(r"\b(?:award.winning|won (?:gold|silver|bronze))\b", description, re.I):
        if not re.search(r"\b(?:award|winner|won|gold|silver|bronze)\b", source, re.I):
            return False
    return True


class ExternalDiscovery:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.policy = SourcePolicy()

    def search(
        self, brief: StoredBrief, strategy: SearchStrategy, progress: ProgressCallback | None = None
    ) -> SearchOutcome:
        if not self.settings.search_configured:
            raise AppError("openai_not_configured", "Configure the OpenAI key and model settings first.", 503)
        budget = RequestBudget(self.settings)
        strategy = strategy.model_copy(
            update={"preferred_sources": normalize_sources(strategy.preferred_sources)}
        )
        outcome = SearchOutcome(
            coverage=[
                SourceCoverage(
                    category=category,
                    note=self.policy.sources[category]["note"],
                )
                for category in strategy.preferred_sources
            ]
        )
        coverage = {item.category: item for item in outcome.coverage}
        notes: dict[str, SourceNote] = {}
        jobs = self.policy.jobs(strategy, self.settings.search_query_limit, brief)
        prompt = (ROOT / "prompts/search.txt").read_text(encoding="utf-8")
        last_error = None
        try:
            with openai.OpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                timeout=self.settings.search_timeout_seconds,
                max_retries=0,
            ) as client:
                for index, job in enumerate(jobs, 1):
                    if progress:
                        progress(f"searching_{index}_of_{len(jobs)}")
                    before_attempts = budget.usage.requests_attempted
                    tool = {"type": "web_search", "search_context_size": "medium"}
                    if job.domains:
                        tool["filters"] = {"allowed_domains": job.domains}
                    reasoning = (
                        {"reasoning": {"effort": "low"}}
                        if self.settings.openai_search_model.startswith("gpt-5")
                        else {}
                    )
                    payload = {
                        "brief": brief.model_dump(exclude={"id", "created_at"}),
                        "strand": job.strand,
                        "queries": job.queries,
                        "source_categories": job.categories,
                        "source_instructions": job.instructions,
                        "target_works": min(8, brief.requested_count),
                    }
                    try:
                        response = budget.call(
                            lambda **kwargs: client.responses.create(
                                model=self.settings.openai_search_model,
                                instructions=prompt,
                                input=json.dumps(payload),
                                tools=[tool],
                                tool_choice="required",
                                max_tool_calls=1,
                                include=["web_search_call.action.sources"],
                                max_output_tokens=4500,
                                store=False,
                                **reasoning,
                                **kwargs,
                            ),
                            self.settings.openai_search_model,
                            reserve=1,
                        )
                        if response.status != "completed":
                            raise AppError(
                                "incomplete_search",
                                "Search output was incomplete; some source coverage may be missing.",
                                502,
                            )
                        discovered = source_notes(response, job)
                        for note in discovered:
                            url = str(note.evidence.source_url)
                            previous = notes.get(url)
                            if previous is None or (
                                previous.evidence.evidence_type == "source_listing"
                                and note.evidence.evidence_type == "search_summary"
                            ):
                                notes[url] = note
                        # Prefer cited work descriptions when limiting the candidate pool.
                        ordered = sorted(
                            notes.items(), key=lambda pair: pair[1].evidence.evidence_type != "search_summary"
                        )
                        notes = dict(ordered[: self.settings.search_candidate_limit])
                        if not discovered:
                            outcome.partial = True
                            outcome.warnings.append(
                                f"No traceable sources returned for {', '.join(job.categories)}."
                            )
                    except AppError as error:
                        last_error = error
                        outcome.partial = True
                        outcome.warnings.append(
                            f"{', '.join(job.categories)}: {error.message} ({error.code})"
                        )
                        if error.code in {
                            "search_budget_exhausted",
                            "openai_authentication",
                            "openai_permission",
                            "openai_quota",
                            "openai_request_failed",
                        }:
                            break
                    finally:
                        if budget.usage.requests_attempted > before_attempts:
                            for category in job.categories:
                                coverage[category].queries_attempted += 1
                if progress:
                    progress("checking_public_pages")
                verifier = PageVerifier(self.settings)
                checks = verifier.check_many(
                    list(notes),
                    deadline=budget.started + self.settings.search_run_timeout_seconds - 15,
                )
                budget.usage.page_requests = verifier.requests
                budget.usage.page_checks = verifier.checked
                budget.usage.accessible_pages = sum(check.status == "accessible" for check in checks.values())
                outcome.page_checks = [check.model_copy(update={"text": ""}) for check in checks.values()]
                for url, note in notes.items():
                    note.page = checks.get(url)
                    if note.page and note.page.status == "accessible" and note.page.work_page:
                        note.page_evidence = Evidence(
                            source_url=note.page.final_url or note.evidence.source_url,
                            page_title=note.page.title or note.evidence.page_title,
                            excerpt=note.page.text,
                            retrieved_at=note.page.checked_at,
                            access_status="accessible",
                            evidence_type="page_text",
                        )
                outcome.evidence = [note.evidence for note in notes.values()] + [
                    note.page_evidence for note in notes.values() if note.page_evidence
                ]
                described = [
                    note
                    for note in notes.values()
                    if (note.evidence.evidence_type == "search_summary" or note.page_evidence)
                    and (not note.page or note.page.status not in {"blocked", "unreachable"})
                    and (not note.page or note.page.status != "accessible" or note.page.work_page)
                ]
                if not described:
                    outcome.error_code = last_error.code if last_error else "no_search_evidence"
                    outcome.warnings.append(
                        "No usable work evidence remained after source checks. Try other queries or sources."
                    )
                    return outcome
                if progress:
                    progress("organizing_search_evidence")
                context = {
                    "brief": brief.model_dump(exclude={"id", "created_at"}),
                    "strategy": strategy.model_dump(),
                    "candidate_limit": min(MAX_REFERENCES, max(8, brief.requested_count * 2)),
                    "sources": [
                        {
                            "id": (note.page_evidence or note.evidence).id,
                            "title": (note.page_evidence or note.evidence).page_title,
                            "url": str((note.page_evidence or note.evidence).source_url),
                            "search_summary": note.evidence.excerpt,
                            "search_evidence_id": note.evidence.id,
                            "page_text": note.page_evidence.excerpt if note.page_evidence else "",
                            "page_check": note.page.model_dump(mode="json", exclude={"text"})
                            if note.page
                            else None,
                            "source_categories": note.categories,
                            "strand": note.strand,
                        }
                        for note in described
                    ],
                }
                with safe_openai_errors():
                    response = budget.call(
                        lambda **kwargs: client.responses.parse(
                            model=self.settings.openai_model,
                            instructions=(ROOT / "prompts/discovery.txt").read_text(encoding="utf-8"),
                            input=json.dumps(context),
                            text_format=DiscoveryDraft,
                            max_output_tokens=1400 + context["candidate_limit"] * 700,
                            store=False,
                            **kwargs,
                        ),
                        self.settings.openai_model,
                    )
                if response.status != "completed" or response.output_parsed is None:
                    raise AppError(
                        "invalid_model_output",
                        "The model could not produce a valid evidence-grounded shortlist.",
                        502,
                    )
                self.build_results(outcome, response.output_parsed, notes, brief, strategy)
                if progress:
                    progress("ranking_and_deduplicating")
                eligible = [
                    item for item in outcome.results if score_reference(item, brief, set(item.evidence_ids))
                ]
                merged = merge_works(eligible)
                if len(merged) < len(eligible):
                    outcome.warnings.append(
                        f"Merged {len(eligible) - len(merged)} duplicate works; alternate links are kept."
                    )
                outcome.results = diverse_shortlist(
                    merged, min(brief.requested_count, self.settings.max_references)
                )
                if len(eligible) < len(response.output_parsed.references):
                    outcome.warnings.append(
                        "Excluded proposed works lacking relevance, specificity or matching evidence."
                    )
                for category in outcome.coverage:
                    category.references_returned = sum(
                        item.source_category == category.category for item in outcome.results
                    )
        except AppError as error:
            outcome.error_code = error.code
            outcome.partial = True
            outcome.warnings.append(error.message)
        finally:
            outcome.usage = budget.finish()
        if outcome.results:
            outcome.error_code = None
            if any(item.verification_status != "supported" for item in outcome.results):
                outcome.partial = True
                outcome.warnings.append(
                    "Some references use search evidence because their original pages could not be checked."
                )
            if len(outcome.results) < brief.requested_count:
                outcome.partial = True
                outcome.warnings.append(
                    f"Returned {len(outcome.results)} references; target was {brief.requested_count}."
                )
        else:
            outcome.error_code = outcome.error_code or "no_eligible_references"
        outcome.warnings.append(
            "Page checks cover readable source text and work identity. They do not establish video analysis, "
            "complete production details or permission to reproduce images and videos."
        )
        for item in outcome.coverage:
            if item.queries_attempted == 0:
                outcome.partial = True
                outcome.warnings.append(f"{item.category} was not searched within this run's budget.")
        return outcome

    def build_results(self, outcome, draft, notes, brief, strategy):
        by_id = {
            evidence.id: note
            for note in notes.values()
            for evidence in [note.evidence, note.page_evidence]
            if evidence
        }
        discarded = 0
        for candidate in draft.references:
            primary = by_id.get(candidate.primary_evidence_id)
            if primary is None or (
                not primary.page_evidence and primary.evidence.evidence_type != "search_summary"
            ):
                discarded += 1
                continue
            if primary.page and primary.page.status in {"blocked", "unreachable"}:
                discarded += 1
                continue
            if primary.page and primary.page.status == "accessible" and not primary.page.work_page:
                discarded += 1
                continue
            evidence = primary.page_evidence or primary.evidence
            title = normalized_words(candidate.title)
            supporting_text = normalized_words(evidence.page_title + " " + evidence.excerpt)
            url = str(evidence.source_url)
            category = self.policy.category(
                str(primary.evidence.source_url),
                candidate.platform,
                strategy.preferred_sources,
                primary.categories,
            )
            if primary.page_evidence and title not in normalized_words(
                primary.page.title + " " + primary.page.text
            ):
                discarded += 1
                continue
            if not title or title not in supporting_text or category is None:
                discarded += 1
                continue
            if not supported_description(
                candidate.annotation.what_it_is, evidence.page_title + " " + evidence.excerpt
            ):
                discarded += 1
                continue
            creator = candidate.creator if normalized_words(candidate.creator) in supporting_text else ""
            annotation = candidate.annotation.model_copy(deep=True)
            annotation.confidence = "medium" if primary.page_evidence else "low"
            if not annotation.production_note.lower().startswith("early indication"):
                annotation.production_note = "Early indication: " + annotation.production_note
            annotation.production_note = annotation.production_note[:6000]
            annotation.rights_note = (
                "Link for reference discussion only. Image and video reproduction "
                "permission has not been established."
            )
            annotation.assumptions = list(
                dict.fromkeys(
                    annotation.assumptions
                    + [
                        (
                            "Source-page text and identity were checked; the video itself was not analysed."
                            if primary.page_evidence
                            else "Search evidence only; original page identity still needs review."
                        ),
                    ]
                )
            )[:10]
            valid_strands = {strand.name for strand in strategy.strands}
            ids = list(
                dict.fromkeys(
                    [evidence.id, primary.evidence.id]
                    + [
                        item_id
                        for item_id in candidate.supporting_evidence_ids
                        if item_id in by_id
                        and title
                        in normalized_words(
                            (by_id[item_id].page_evidence or by_id[item_id].evidence).excerpt
                            + " "
                            + (by_id[item_id].page_evidence or by_id[item_id].evidence).page_title
                        )
                    ]
                )
            )
            outcome.results.append(
                ReferenceItem(
                    id=new_id(),
                    title=candidate.title,
                    creator=creator,
                    platform=publishing_platform(url),
                    source_category=category,
                    source_url=evidence.source_url,
                    tags=candidate.tags,
                    strand=candidate.strand if candidate.strand in valid_strands else primary.strand,
                    evidence_ids=ids,
                    verification_status="supported" if primary.page_evidence else "unverified",
                    is_mock=False,
                    annotation=annotation,
                    assessment=candidate.assessment,
                    page_check=primary.page.model_copy(update={"text": ""}) if primary.page else None,
                    alternate_urls=[primary.evidence.source_url]
                    if str(primary.evidence.source_url) != url
                    else [],
                )
            )
        if discarded:
            outcome.warnings.append(
                f"Excluded {discarded} proposed references without matching titles, sources or evidence IDs."
            )
