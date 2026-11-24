"""Collect source-declared article images without executing page scripts."""

import re
from urllib.parse import urljoin, urlsplit

from backend.services.page_verification import PublicHTML
from backend.services.source_policy import canonical_url

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
EXCLUDED_TAGS = {
    "head",
    "header",
    "nav",
    "footer",
    "aside",
    "form",
    "script",
    "style",
    "template",
    "noscript",
}
DECORATIVE = re.compile(
    r"(?:^|[\s_-])(?:logo|avatar|icon|sprite|tracking|spacer|pixel|advertisement|advert|ads|"
    r"social|share|related|sidebar|navigation|menu|cookie|author|sponsor|recommended)(?:$|[\s_-])",
    re.I,
)
ASSET_NOISE = re.compile(
    r"(?:^|[\s_./-])(?:logo|avatar|icon|sprite|tracking|spacer|pixel|advertisement|advert|ads|cookie)"
    r"(?:$|[\s_./-])", re.I,
)


def decorative_image_url(url):
    # Names such as social-campaign.jpg and project-banner.jpg can be real work.
    return bool(ASSET_NOISE.search(urlsplit(url).path.rsplit("/", 1)[-1]))


def dimension(value):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)(?:px)?\s*", value or "")
    return float(match[1]) if match else None


class PreviewHTML(PublicHTML):
    """Prefer article/main images in document order, with a bounded body fallback."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.frames = []
        self.image_candidates = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        data = dict(attrs)
        parent_blocked, scope = self.frames[-1][1:] if self.frames else (False, 0)
        marker = " ".join(str(data.get(key) or "") for key in ("class", "id", "role"))
        blocked = (
            parent_blocked
            or tag in EXCLUDED_TAGS
            or "hidden" in data
            or data.get("aria-hidden") == "true"
            or bool(DECORATIVE.search(marker))
            or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", data.get("style") or "", re.I))
        )
        if tag == "article":
            scope = 2
        elif tag == "main" or data.get("role") == "main":
            scope = max(scope, 1)
        if tag not in VOID_TAGS:
            self.frames.append((tag, blocked, scope))
        if tag != "img" or blocked or len(self.image_candidates) >= 40:
            return
        if ASSET_NOISE.search(data.get("alt") or ""):
            return
        width, height = dimension(data.get("width")), dimension(data.get("height"))
        if (width is not None and width < 160) or (height is not None and height < 90):
            return
        sources = [data.get(key) or "" for key in ("data-src", "data-original", "data-lazy-src")]
        for key in ("data-srcset", "srcset"):
            srcset = data.get(key) or ""
            if "data:" in srcset.lower():
                continue
            for entry in srcset.split(",")[:10]:
                parts = entry.split()
                if parts:
                    if len(parts) == 2 and parts[1].endswith("w"):
                        size = dimension(parts[1][:-1])
                        if size is not None and size < 160:
                            continue
                    sources.append(parts[0])
        sources.append(data.get("src") or "")
        for value in sources:
            if not value.strip():
                continue
            image_url = canonical_url(urljoin(self.base_url, value.strip()))
            if not image_url or urlsplit(image_url).path.lower().endswith((".svg", ".ico")):
                continue
            if decorative_image_url(image_url):
                continue
            self.image_candidates.append((scope, image_url))
            break

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        for index in range(len(self.frames) - 1, -1, -1):
            if self.frames[index][0] == tag:
                del self.frames[index:]
                break

    def body_images(self):
        if not self.image_candidates:
            return []
        scope = max(scope for scope, _ in self.image_candidates)
        return list(dict.fromkeys(url for rank, url in self.image_candidates if rank == scope))
