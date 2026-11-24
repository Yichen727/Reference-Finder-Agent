"""Render current bullet-form annotations and historical prose without rewriting records."""

import re


def borrow_points(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    marker = re.compile(r"^(?:[-*•‣]\s*|\d+[.)]\s+)")
    if any(marker.match(line) for line in lines):
        points = []
        for line in lines:
            if marker.match(line):
                points.append(marker.sub("", line).strip())
            elif points:
                points[-1] += " " + line
            else:
                points.append(line)
    else:
        # Do not invent actions or drop existing advice when rendering old prose.
        points = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", " ".join(lines))
    points = [point for point in points if point]
    if len(points) <= 3:
        return points
    return [" ".join(points[index * len(points) // 3:(index + 1) * len(points) // 3])
            for index in range(3)]
