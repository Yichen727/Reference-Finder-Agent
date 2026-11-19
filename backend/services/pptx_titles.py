"""Local, export-only title fitting and complete-word truncation."""

import math
from dataclasses import dataclass

from PIL import ImageFont


@dataclass(frozen=True)
class TitleLayout:
    width: float

    def font_size(self, title: str) -> float:
        title = title.upper()  # Measure exactly the uppercase text placed in the template.
        font = ImageFont.load_default(size=54 * 4)
        measured = max(font.getlength(title) * 1.15 / 4, len(title) * 54 * 0.65)
        return min(54, math.floor(54 * self.width / max(measured, 1) * 2) / 2)

    def fits(self, title: str) -> bool:
        return bool(title.strip()) and self.font_size(title) >= 18

    def truncate(self, title: str) -> str:
        """Keep the longest complete-word prefix that fits, including its ellipsis."""
        if self.fits(title):
            return title
        words = title.split()
        low, high = 0, len(words)
        while low < high:
            middle = (low + high + 1) // 2
            if self.fits(" ".join(words[:middle]) + "…"):
                low = middle
            else:
                high = middle - 1
        return " ".join(words[:low]) + "…"
