"""Load and validate the eight-category source configuration without network calls."""

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter, ValidationError

from backend.core.errors import AppError
from backend.schemas.source_registry import (
    SourceCategory,
    SourceCategoryMetadata,
    SourceDefinition,
    SourceGroup,
)


def configuration_error(filename: str, reason: str):
    return AppError(
        "source_configuration_invalid", f"Invalid source configuration '{filename}': {reason}", 503
    )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_configuration(filename: str, directory: Path | None):
    try:
        resource = (directory if directory is not None else files("config")).joinpath(filename)
        value = json.loads(resource.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object keyed by source category")
        return value
    except (OSError, ValueError, ModuleNotFoundError) as error:
        reason = str(error) if isinstance(error, ValueError) else "The configuration resource is unavailable"
        raise configuration_error(filename, reason) from None


def validate_configuration(filename, value, contract, expected_names):
    if set(value) != set(expected_names):
        raise configuration_error(filename, "Category names must match the registered source contract")
    try:
        return TypeAdapter(contract).validate_python(value)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        raise configuration_error(filename, f"{location}: {first['msg']}") from None


@dataclass
class SourceRegistry:
    categories: dict[SourceCategory, SourceDefinition]

    def metadata(self) -> list[SourceCategoryMetadata]:
        return [
            SourceCategoryMetadata(
                name=name, **definition.model_dump(), open_discovery=not definition.domains
            )
            for name, definition in self.categories.items()
        ]


def load_source_registry(directory: Path | None = None) -> SourceRegistry:
    names = get_args(SourceCategory)
    categories = validate_configuration(
        "sources.json",
        read_configuration("sources.json", directory),
        dict[SourceCategory, SourceDefinition],
        names,
    )
    if {definition.group for definition in categories.values()} != set(get_args(SourceGroup)):
        raise configuration_error("sources.json", "Each registered source group must occur exactly once")
    if any(
        (not definition.domains) != (definition.group == "original_work")
        for definition in categories.values()
    ):
        raise configuration_error(
            "sources.json", "Only original_work uses an empty domain list for open discovery"
        )
    return SourceRegistry(categories)
