"""Packaged source configuration shared by source and installed distributions."""

# Compatibility labels for existing research and in-memory frontend drafts.
SOURCE_CATEGORY_RENAMES = {
    "Video & Film": "Video Platforms",
    "Awards & Winning Work": "Award Archives",
    "Creative Publications": "Creative Press",
    "Brand & Corporate Films": "Brand Film Showcases",
    "Studios & Production Companies": "Studios & Agencies",
    "Web & Interactive Design": "Web & Interactive",
    "Social & Visual Inspiration": "Social Platforms",
}


def current_source_name(value: str) -> str:
    return SOURCE_CATEGORY_RENAMES.get(value, value)
