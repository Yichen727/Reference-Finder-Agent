from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

MAX_REFERENCES = 15
Text = Annotated[str, Field(max_length=6000)]
Source = Literal[
    "Vimeo",
    "YouTube",
    "Instagram",
    "Pinterest",
    "Studio portfolios",
    "Production companies",
    "Award archives",
]
SOURCES: list[Source] = [
    "Vimeo",
    "YouTube",
    "Instagram",
    "Pinterest",
    "Studio portfolios",
    "Production companies",
    "Award archives",
]


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReferenceBrief(Model):
    concept: str = Field(min_length=1, max_length=6000)
    purpose: Text = ""
    audience: Text = ""
    format: Text = ""
    platform: Text = ""
    qualities: Text = ""
    constraints: Text = ""
    known_references: Text = ""
    exclusions: Text = ""
    language: Literal["en"] = "en"
    preferred_sources: list[Source] = Field(
        default_factory=lambda: SOURCES.copy(), min_length=1, max_length=7
    )
    requested_count: int = Field(default=12, ge=1, le=MAX_REFERENCES)


class StoredBrief(ReferenceBrief):
    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=now)


class QueryStrand(Model):
    name: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1, max_length=1000)
    queries: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=4)


class StrategyDraft(Model):
    summary: str = Field(min_length=1, max_length=2000)
    assumptions: list[Text] = Field(default_factory=list, max_length=10)
    strands: list[QueryStrand] = Field(min_length=1, max_length=5)


class SearchStrategy(StrategyDraft):
    brief_id: str
    preferred_sources: list[Source] = Field(min_length=1, max_length=7)
    # Retained to read archived records and preserve the existing API shape.
    is_mock: bool = False


class Evidence(Model):
    id: str = Field(default_factory=new_id)
    source_url: HttpUrl
    page_title: Text
    excerpt: Text
    retrieved_at: datetime = Field(default_factory=now)
    access_status: Literal["mock", "unverified", "accessible", "restricted", "unreachable"] = "unverified"
    evidence_type: Literal["mock", "search_snippet", "search_summary", "source_listing", "page_text"] = (
        "source_listing"
    )


class Annotation(Model):
    what_it_is: Text
    what_to_borrow: Text
    why_it_fits: Text
    production_note: Text
    assumptions: list[Text] = Field(default_factory=list, max_length=10)
    confidence: Literal["low", "medium", "high"] = "low"
    rights_note: Text = "No image or video reproduction permission has been established."


class ApprovedThumbnail(Model):
    url: HttpUrl
    source_url: HttpUrl
    usage_status: Literal["approved"]
    available: bool = False


class PageCheck(Model):
    original_url: Text
    final_url: HttpUrl | None = None
    status: Literal["accessible", "restricted", "unreachable", "unverified", "blocked"] = "unverified"
    reason: Text = ""
    http_status: int | None = None
    title: Text = ""
    text: Text = ""
    canonical_url: HttpUrl | None = None
    media_urls: list[HttpUrl] = Field(default_factory=list, max_length=12)
    work_page: bool = False
    checked_at: datetime = Field(default_factory=now)


class CreativeAssessment(Model):
    relevance: int = Field(ge=0, le=5)
    specificity: int = Field(ge=0, le=5)
    craft: int = Field(ge=0, le=5)
    feasibility: int = Field(ge=0, le=5)
    role: Literal["close_match", "execution", "aspirational", "unexpected"]
    reasons: list[Text] = Field(min_length=1, max_length=5)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class RankingDetails(Model):
    score: float = Field(ge=0, le=100)
    components: dict[str, float] = Field(max_length=5)
    reasons: list[Text] = Field(max_length=10)
    role: Literal["close_match", "execution", "aspirational", "unexpected"] = "close_match"
    selection_note: Text = ""


class ReferenceItem(Model):
    id: str
    title: Text
    creator: Text = ""
    source_type: Literal["external", "internal"] = "external"
    platform: Source
    source_url: HttpUrl
    tags: list[Text] = Field(max_length=20)
    strand: Text
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    verification_status: Literal["mock", "unverified", "supported"] = "unverified"
    is_mock: bool = False
    thumbnail: ApprovedThumbnail | None = None
    alternate_urls: list[HttpUrl] = Field(default_factory=list, max_length=20)
    page_check: PageCheck | None = None
    ranking: RankingDetails | None = None
    assessment: CreativeAssessment | None = None
    merged_count: int = Field(default=1, ge=1)
    annotation: Annotation


class SelectionInput(Model):
    ordered_ids: list[str] = Field(max_length=MAX_REFERENCES)
    annotation_overrides: dict[str, Annotation] = Field(default_factory=dict, max_length=MAX_REFERENCES)
    rejection_notes: Text = ""
    expected_version: int = Field(ge=0)
    rejected_ids: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)

    @field_validator("ordered_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Selection IDs must be unique")
        return value

    @model_validator(mode="after")
    def distinct_rejections(self):
        if len(self.rejected_ids) != len(set(self.rejected_ids)) or set(self.rejected_ids) & set(
            self.ordered_ids
        ):
            raise ValueError("Rejected references must be unique and cannot be selected")
        return self


class Selection(SelectionInput):
    version: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=now)


class SearchRequest(Model):
    brief_id: str
    strategy: SearchStrategy
    idempotency_key: str = Field(min_length=1, max_length=100)


class UsageStats(Model):
    requests_attempted: int = Field(default=0, ge=0)
    requests_succeeded: int = Field(default=0, ge=0)
    web_search_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    models: list[str] = Field(default_factory=list, max_length=10)
    page_checks: int = Field(default=0, ge=0)
    page_requests: int = Field(default=0, ge=0)
    accessible_pages: int = Field(default=0, ge=0)


class SourceCoverage(Model):
    category: Source
    queries_attempted: int = Field(default=0, ge=0)
    references_returned: int = Field(default=0, ge=0)
    note: Text = ""


class SearchOutcome(Model):
    results: list[ReferenceItem] = Field(default_factory=list, max_length=MAX_REFERENCES)
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageStats = Field(default_factory=UsageStats)
    coverage: list[SourceCoverage] = Field(default_factory=list, max_length=7)
    partial: bool = False
    error_code: str | None = None
    page_checks: list[PageCheck] = Field(default_factory=list, max_length=60)


class DiscoveredReference(Model):
    # URLs come from trusted tool metadata, never from this model-generated object.
    title: str = Field(min_length=1, max_length=500)
    creator: str = Field(max_length=500)
    primary_evidence_id: str
    supporting_evidence_ids: list[str] = Field(max_length=5)
    platform: Source
    tags: list[Annotated[str, Field(max_length=100)]] = Field(max_length=10)
    strand: str = Field(max_length=120)
    annotation: Annotation
    assessment: CreativeAssessment | None = None


class DiscoveryDraft(Model):
    references: list[DiscoveredReference] = Field(max_length=MAX_REFERENCES)


class SearchRun(Model):
    id: str = Field(default_factory=new_id)
    brief_id: str
    strategy: SearchStrategy
    mode: Literal["mock", "live"]
    status: Literal["queued", "running", "completed", "partial", "failed", "interrupted"] = "queued"
    stage: str = "queued"
    results: list[ReferenceItem] = Field(default_factory=list, max_length=MAX_REFERENCES)
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    usage: UsageStats = Field(default_factory=UsageStats)
    coverage: list[SourceCoverage] = Field(default_factory=list, max_length=7)
    page_checks: list[PageCheck] = Field(default_factory=list, max_length=60)
    selection: Selection | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    @model_validator(mode="after")
    def valid_references(self):
        ids = [item.id for item in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate references")
        evidence_ids = {item.id for item in self.evidence}
        if any(not set(item.evidence_ids) <= evidence_ids for item in self.results):
            raise ValueError("Reference cites missing evidence")
        return self


class ExportRecord(Model):
    id: str = Field(default_factory=new_id)
    run_id: str
    selection_version: int
    filename: str
    created_at: datetime = Field(default_factory=now)


class ExportRequest(Model):
    expected_selection_version: int | None = Field(default=None, ge=1)
