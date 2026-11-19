"""Resolve result-filter categories without changing saved reference metadata."""

import json
from importlib.resources import files
from urllib.parse import urlsplit

from config import current_source_name


def filter_definitions(cached_categories=None):
    if cached_categories:
        return cached_categories
    # Saved research must remain browsable if the new-search metadata endpoint is unavailable.
    definitions = json.loads(files("config").joinpath("sources.json").read_text(encoding="utf-8-sig"))
    return [{"name": name, **definition} for name, definition in definitions.items()]


def reference_category(item, categories):
    names = {category["name"] for category in categories}
    # New results carry the category; older records may store it as their platform label.
    for field in ("source_category", "platform"):
        name = current_source_name(item.get(field) or "")
        if name in names:
            return name
    try:
        host = (urlsplit(str(item.get("source_url", ""))).hostname or "").lower().rstrip(".")
    except ValueError:
        host = ""
    platform = str(item.get("platform", "")).lower().removeprefix("www.")
    platform = {
        "youtube": "youtube.com", "vimeo": "vimeo.com",
        "instagram": "instagram.com", "pinterest": "pinterest.com",
    }.get(platform, platform)
    for candidate in (host, platform):
        for category in categories:
            if any(candidate == domain or candidate.endswith("." + domain)
                   for domain in category.get("domains", [])):
                return category["name"]
    # Unknown historical sites remain visible without inventing a source category.
    return None


def normalize_filter_values(values, results, categories_by_id, options):
    """Translate an existing platform/domain filter into the new category choices."""
    normalized = []
    for value in values:
        name = current_source_name(value)
        if name in options:
            normalized.append(name)
        else:
            normalized.extend(
                categories_by_id[item["id"]]
                for item in results
                if item["platform"].casefold() == value.casefold()
                and categories_by_id[item["id"]] in options
            )
    return list(dict.fromkeys(normalized))
