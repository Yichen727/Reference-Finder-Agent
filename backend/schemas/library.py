"""Persistent reference snapshots, global favorites and cross-research packs."""

from datetime import datetime

from pydantic import Field, model_validator

from backend.core.limits import MAX_REFERENCES
from backend.schemas.models import (
    Annotation,
    Evidence,
    Model,
    ReferenceItem,
    SearchStrategy,
    StoredBrief,
    new_id,
    now,
)


class ReferenceLocator(Model):
    run_id: str | None = None
    reference_id: str | None = None
    saved_reference_id: str | None = None
    annotation: Annotation | None = None

    @model_validator(mode="after")
    def one_source(self):
        research = bool(self.run_id and self.reference_id)
        saved = bool(self.saved_reference_id)
        if research == saved or bool(self.run_id) != bool(self.reference_id):
            raise ValueError("Choose a research reference or a saved reference")
        return self


class ReferenceSnapshot(Model):
    run_id: str
    batch_run_id: str | None = None
    reference_id: str
    reference: ReferenceItem
    evidence: list[Evidence]
    brief: StoredBrief
    strategy: SearchStrategy
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_evidence(self):
        if not set(self.reference.evidence_ids) <= {item.id for item in self.evidence}:
            raise ValueError("Saved reference cites missing evidence")
        return self


class SavedReference(Model):
    id: str = Field(default_factory=new_id)
    snapshots: list[ReferenceSnapshot] = Field(min_length=1)
    favorite: bool = False
    favorite_snapshot: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=now)

    @model_validator(mode="after")
    def valid_favorite(self):
        if self.favorite_snapshot >= len(self.snapshots):
            raise ValueError("Favorite context is unavailable")
        return self


class ReferencePack(Model):
    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=100)
    ordered_ids: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)
    references: dict[str, ReferenceSnapshot] = Field(default_factory=dict, max_length=MAX_REFERENCES)
    version: int = Field(default=1, ge=1)
    imported_run_id: str | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    @model_validator(mode="after")
    def valid_order(self):
        if len(self.ordered_ids) != len(set(self.ordered_ids)) or set(self.ordered_ids) != set(
            self.references
        ):
            raise ValueError("Pack order must contain every saved reference exactly once")
        return self


class PackCreateInput(Model):
    name: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class PackVersionInput(Model):
    expected_version: int = Field(ge=1)


class PackRenameInput(PackVersionInput):
    name: str = Field(min_length=1, max_length=100)


class PackOrderInput(PackVersionInput):
    ordered_ids: list[str] = Field(max_length=MAX_REFERENCES)

    @model_validator(mode="after")
    def unique_order(self):
        if len(self.ordered_ids) != len(set(self.ordered_ids)):
            raise ValueError("Pack order must contain unique reference IDs")
        return self


class PackReferenceInput(ReferenceLocator):
    expected_version: int = Field(ge=1)
