"""Eight source categories, their display-name aliases and configuration contracts."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceCategory = Literal[
    "Video Platforms",
    "Award Archives",
    "Creative Press",
    "Brand Film Showcases",
    "Studios & Agencies",
    "Web & Interactive",
    "Podcasts & Interviews",
    "Social Platforms",
]
PreviousSourceCategory = Literal[
    "Video & Film",
    "Awards & Winning Work",
    "Creative Publications",
    "Brand & Corporate Films",
    "Studios & Production Companies",
    "Web & Interactive Design",
    "Social & Visual Inspiration",
]
StoredSourceCategory = SourceCategory | PreviousSourceCategory
SourceGroup = Literal[
    "video", "awards", "editorial", "brand_films", "original_work", "interactive", "podcast", "social"
]


def valid_domain(value: str) -> bool:
    return (
        len(value) <= 253
        and value == value.lower()
        and len(value.split(".")) > 1
        and re.fullmatch(r"[a-z]{2,63}", value.split(".")[-1]) is not None
        and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split("."))
        and not value.endswith((".localhost", ".local", ".internal", ".invalid", ".lan"))
    )


class SourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    group: SourceGroup
    domains: list[str] = Field(max_length=50)
    instruction: str = Field(min_length=1, max_length=2000)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("domains")
    @classmethod
    def bare_unique_domains(cls, value):
        if len(value) != len(set(value)) or any(not valid_domain(domain) for domain in value):
            raise ValueError("Use unique, lowercase public domain names without schemes, paths or wildcards")
        return value


class SourceCategoryMetadata(SourceDefinition):
    name: SourceCategory
    open_discovery: bool
    default_selected: bool = True
