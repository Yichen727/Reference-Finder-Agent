"""Website-independent work-media candidates from public, inert HTML."""

import json
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from lxml import etree, html

from backend.services.source_policy import canonical_url

NOISE = re.compile(
    r"(?:^|[\s_./-])(?:logo|favicon|avatar|icon|sprite|tracking|spacer|pixel|advertisement|advert|ads|cookie)"
    r"(?:$|[\s_./-])", re.I,
)
COMPONENT_NOISE = re.compile(
    r"(?:^|[\s_-])(?:above[-_]nav|advertisement|advert|ads|promo|promotional|related|recommended|"
    r"recommendations|sidebar|share[-_]buttons|cookie[-_]banner|author|byline|headshot|profile)(?:$|[\s_-])",
    re.I,
)
PROJECT = re.compile(
    r"(?:^|[\s_-])(?:project|work|entry|gallery|swiper|hero|article|content|media)(?:$|[\s_-])"
)
RASTER = re.compile(r"\.(?:jpe?g|png|webp|avif|gif)(?:$|[?#])", re.I)
GENERIC = re.compile(
    r"^(?:social|social[-_]share|share[-_]image|og[-_]image|default(?:[-_]\w+)?|fallback|"
    r"site[-_](?:image|cover)|(?:yellow|graphite|black|wood|bronze|silver|gold)[-_](?:pencil|award|trophy))"
    r"\.(?:jpe?g|png|webp|avif|gif)$", re.I,
)


def decorative_url(url):
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return bool(NOISE.search(name) or GENERIC.fullmatch(name))


def image_identity(url):
    """Treat responsive/CDN transformations of one image as one asset."""
    parts = urlsplit(url)
    transforms = {"w", "width", "h", "height", "dpr", "fit", "crop", "auto", "fm", "format",
                  "q", "quality", "ar"}
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in transforms]
    path = re.sub(r"([a-f0-9-]{24,})-(?:small|medium|large)(?=\.)", r"\1", parts.path, flags=re.I)
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    kind: str
    score: int
    label: str = ""

    @property
    def basis(self):
        return {"body": "Relevant article or project gallery image",
                "poster": "Project video poster", "structured": "Project structured image metadata",
                "metadata": "Website-declared preview image", "background": "Project background image"}[
                    self.kind]


class CandidateCollector:
    def __init__(self, markup, base_url, title=""):
        self.base_url, self.title = base_url, title
        self.tokens = set(re.findall(r"[a-z0-9]{4,}", title.lower())) - {
            "video", "film", "with", "from", "work", "awards", "archive", "official",
        }
        try:
            self.tree = html.fromstring(markup or "<html></html>")
        except (etree.ParserError, ValueError):
            self.tree = html.fromstring("<html></html>")
        self.images, self.media = [], []
        self.rejections = Counter()
        self.has_media_markup = bool(self.tree.xpath("//iframe|//video"))

    def context(self, node):
        """Exclude local irrelevant components; global theme/body classes carry no veto."""
        score = 25
        ancestors = [node, *node.iterancestors()]
        in_content = any(p.tag in {"article", "main"} for p in ancestors)
        for index, parent in enumerate(ancestors):
            if parent.tag in {"html", "body"}:
                break
            marker = " ".join(parent.get(k, "") for k in ("class", "id", "role")).lower()
            if parent.tag in {"nav", "footer", "aside", "form", "script", "style", "template"}:
                return None
            if parent.tag not in {"main", "article"} and COMPONENT_NOISE.search(marker):
                return None
            if ("hidden" in parent.attrib or parent.get("aria-hidden") == "true"
                    or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", parent.get("style", ""))):
                # Offscreen gallery slides are valid images; hidden page widgets are not.
                if not re.search(r"swiper[-_]slide|gallery[-_]item", marker):
                    return None
            if parent.tag == "header" and not in_content:
                return None
            if parent.tag == "article":
                score = max(score, 75)
            elif parent.tag == "main" or parent.get("role") == "main":
                score = max(score, 55)
            if PROJECT.search(marker):
                score = max(score, 65)
            if re.search(r"(?:^|[\s_-])(?:gallery|swiper)(?:$|[\s_-])", marker):
                score = max(score, 80)
            if parent.tag == "figure":
                score = max(score, 70)
            if parent.tag == "a" and parent.get("href"):
                link = canonical_url(urljoin(self.base_url, parent.get("href")))
                if link and link != canonical_url(self.base_url) and not RASTER.search(link):
                    if re.search(r"/(?:work|projects?|news|winners?[-_]gallery|authors?|profiles?)/",
                                 urlsplit(link).path):
                        return None
            if index == 0 and NOISE.search(marker + " " + parent.get("alt", "")):
                return None
            if index <= 2 and parent.tag in {"div", "a", "span"} and NOISE.search(marker):
                return None
        return score

    def add(self, url, kind, score, label=""):
        if not isinstance(url, str) or not url.strip():
            return
        clean = canonical_url(urljoin(self.base_url, str(url or "").strip()))
        if not clean or decorative_url(clean) or urlsplit(clean).path.lower().endswith((".svg", ".ico")):
            self.rejections["unsafe_or_decorative_asset"] += 1
            return
        words = set(re.findall(r"[a-z0-9]{4,}", (label + " " + urlsplit(clean).path).lower()))
        relevance = min(16, len(words & self.tokens) * 4)
        self.images.append(MediaCandidate(clean, kind, score + relevance, label[:300]))

    @staticmethod
    def sources(node):
        lazy = [node.get(k, "") for k in ("data-src", "data-original", "data-lazy-src")]
        responsive = []
        picture = next((parent for parent in node.iterancestors() if parent.tag == "picture"), None)
        picture_sources = list(picture.iter("source")) if picture is not None else []
        nodes = [node, *picture_sources]
        for element in nodes:
            for key in ("data-srcset", "srcset"):
                for entry in element.get(key, "").split(",")[:12]:
                    parts = entry.split()
                    if not parts or parts[0].startswith("data:"):
                        continue
                    size = re.fullmatch(r"(\d+(?:\.\d+)?)([wx])", parts[1]) if len(parts) > 1 else None
                    weight = float(size[1]) * (640 if size[2] == "x" else 1) if size else 640
                    if weight >= 160:
                        responsive.append((weight, parts[0]))
        sources = [value for _, value in sorted(responsive, reverse=True)]
        sources.extend(lazy)
        sources.append(node.get("src", ""))
        return list(dict.fromkeys(value for value in sources if value and not value.startswith("data:")))

    def structured(self):
        visited = 0

        def images(value, score, label):
            if isinstance(value, str):
                self.add(value, "structured", score, label)
            elif isinstance(value, list):
                for item in value[:12]:
                    images(item, score, label)
            elif isinstance(value, dict):
                images(value.get("contentUrl") or value.get("url") or value.get("thumbnailUrl"), score, label)

        def walk(value, depth=0, in_work=False):
            nonlocal visited
            visited += 1
            if depth > 8 or visited > 300:
                return
            if isinstance(value, list):
                for child in value[:40]:
                    walk(child, depth + 1, in_work)
            if not isinstance(value, dict):
                return
            kinds = value.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if set(kinds) & {"Organization", "Person", "Brand", "BreadcrumbList", "ItemList"}:
                return
            label = str(value.get("name") or value.get("headline") or "")
            if set(kinds) & {"VideoObject", "ImageObject", "Article", "NewsArticle", "CreativeWork",
                             "ImageGallery", "WebPage", "Photograph"}:
                if NOISE.search(label) or COMPONENT_NOISE.search(label):
                    return
                images(value.get("thumbnailUrl"), 80 if "VideoObject" in kinds else 65, label)
                images(value.get("image"), 70, label)
                if "ImageObject" in kinds or "Photograph" in kinds:
                    image_score = 70 if in_work or label else 35
                    images(value.get("contentUrl") or value.get("url"), image_score, label)
                if "VideoObject" in kinds:
                    url = canonical_url(urljoin(self.base_url, str(value.get("embedUrl") or "")))
                    if url:
                        self.media.append(url)
            for key, child in list(value.items())[:40]:
                if isinstance(child, (dict, list)) and key not in {"logo", "author", "publisher"}:
                    project = bool(set(kinds) & {"VideoObject", "Article", "NewsArticle", "CreativeWork",
                                                 "ImageGallery", "WebPage"})
                    walk(child, depth + 1, in_work or project)

        for node in self.tree.xpath('//script[@type="application/ld+json"]')[:10]:
            try:
                walk(json.loads((node.text or "")[:100_000]))
            except (ValueError, RecursionError, TypeError):
                continue

    def collect(self):
        for node in self.tree.xpath("//img|//video|//iframe|//*[@data-poster or @data-video-poster "
                                    "or @data-plyr-config or @data-player-config or @data-setup]")[:300]:
            score = self.context(node)
            if score is None:
                self.rejections["irrelevant_or_hidden_element"] += 1
                continue
            label = node.get("alt") or node.get("title") or ""
            if NOISE.search(label):
                self.rejections["decorative_label"] += 1
                continue
            if node.tag == "img":
                figure = next((parent for parent in node.iterancestors() if parent.tag == "figure"), None)
                if figure is not None:
                    caption = " ".join(figure.xpath(".//figcaption//text()"))[:300]
                    label = " ".join([label, caption]).strip()
                too_small = any(re.fullmatch(r"\d+(?:px)?", node.get(key, "")) and
                                int(node.get(key).removesuffix("px")) < minimum
                                for key, minimum in (("width", 160), ("height", 90)))
                if too_small:
                    self.rejections["small_declared_dimensions"] += 1
                    continue
                for index, url in enumerate(self.sources(node)[:2]):
                    self.add(url, "body", score - index, label)
            for key in ("poster", "data-poster", "data-video-poster"):
                if node.get(key):
                    self.add(node.get(key), "poster", score + 12, label)
            for key in ("data-plyr-config", "data-player-config", "data-setup"):
                try:
                    config = json.loads(node.get(key, "{}")[:32_000])
                    if isinstance(config, dict):
                        for field in ("poster", "thumbnail", "thumbnailUrl", "thumbnail_url"):
                            if isinstance(config.get(field), str):
                                self.add(config[field], "poster", score + 12, label)
                except (ValueError, TypeError):
                    pass
            if node.tag == "iframe":
                url = canonical_url(urljoin(self.base_url, node.get("src") or node.get("data-src") or ""))
                if url:
                    self.media.append(url)
        for node in self.tree.xpath('//*[@style]')[:200]:
            score = self.context(node)
            if score is not None and score >= 65:
                for url in re.findall(r"background(?:-image)?\s*:[^;]*?url\(['\"]?([^)'\"]+)",
                                      node.get("style", ""), re.I):
                    self.add(url, "background", score)
        for node in self.tree.xpath('//meta[@property or @name]')[:100]:
            key = (node.get("property") or node.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self.add(node.get("content"), "metadata", 50, self.title)
        self.structured()
        unique = {}
        scoped = any(candidate.score >= 50 for candidate in self.images)
        for candidate in self.images:
            if scoped and candidate.score < 50:
                continue
            identity = image_identity(candidate.url)
            if identity not in unique or candidate.score > unique[identity].score:
                unique[identity] = candidate
        return sorted(unique.values(), key=lambda candidate: -candidate.score)[:48]
