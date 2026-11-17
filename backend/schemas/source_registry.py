"""Canonical source categories and legacy labels for backward-compatible research."""

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictBool, field_validator

SourceCategory = Literal[
    "Video & Film",
    "Awards & Winning Work",
    "Creative Publications",
    "Brand & Corporate Films",
    "Studios & Production Companies",
    "Web & Interactive Design",
    "Podcasts & Interviews",
    "Social & Visual Inspiration",
]
SourceGroup = Literal[
    "video", "awards", "editorial", "brand_films", "original_work", "interactive", "podcast", "social"
]
LegacySource = Literal[
    "Vimeo",
    "YouTube",
    "Instagram",
    "Pinterest",
    "Studio portfolios",
    "Production companies",
    "Award archives",
]

LEGACY_CATEGORY_MAP: dict[LegacySource, SourceCategory] = {
    "Vimeo": "Video & Film",
    "YouTube": "Video & Film",
    "Instagram": "Social & Visual Inspiration",
    "Pinterest": "Social & Visual Inspiration",
    "Studio portfolios": "Studios & Production Companies",
    "Production companies": "Studios & Production Companies",
    "Award archives": "Awards & Winning Work",
}


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


class CuratedSeed(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: HttpUrl
    purpose: str = Field(min_length=1, max_length=1000)
    enabled: StrictBool

    @field_validator("url", mode="before")
    @classmethod
    def no_control_characters(cls, value):
        if isinstance(value, str) and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Seed URLs cannot contain control characters")
        return value

    @field_validator("url")
    @classmethod
    def public_source_url(cls, value):
        host = (value.host or "").strip("[]")
        if value.username or value.password or value.fragment or value.port not in {80, 443}:
            raise ValueError("Use a public HTTP(S) page URL without credentials, fragments or custom ports")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not valid_domain(host):
                raise ValueError("Seed URLs must identify a public host") from None
        else:
            if not address.is_global or address.is_reserved or address.is_multicast:
                raise ValueError("Seed URLs must not target private or reserved addresses")
        return value


class SourceCategoryMetadata(SourceDefinition):
    name: SourceCategory
    open_discovery: bool
    default_selected: bool = True
