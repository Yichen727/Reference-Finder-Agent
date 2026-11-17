import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from pydantic import HttpUrl, TypeAdapter

from backend.schemas.models import SearchStrategy, Source, StoredBrief, normalize_sources
from backend.services.source_registry import load_source_registry

URL_ADAPTER = TypeAdapter(HttpUrl)


def public_address(address) -> bool:
    if not address.is_global or any(
        [
            address.is_reserved,
            address.is_multicast,
            address.is_loopback,
            address.is_link_local,
            address.is_unspecified,
        ]
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return public_address(address.ipv4_mapped)
        if address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network("64:ff9b:1::/48"):
            return False
        if address in ipaddress.ip_network("64:ff9b::/96"):
            return public_address(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return True


def canonical_url(value: str) -> str | None:
    try:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            return None
        parts = urlsplit(str(URL_ADAPTER.validate_python(value)))
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
            return None
        if any(char.isspace() for char in host):
            return None
        if host == "localhost" or host.endswith((".localhost", ".local", ".invalid", ".internal", ".lan")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.replace(".", "").isdigit():
                return None
        else:
            if not public_address(address):
                return None
        # Preserve access hashes and work-identifying parameters such as v and h.
        query = [
            part
            for part in parts.query.split("&")
            if part
            and not (
                unquote_plus(part.split("=", 1)[0]).lower().startswith("utm_")
                or unquote_plus(part.split("=", 1)[0]).lower() in {"fbclid", "gclid"}
            )
        ]
        port = parts.port
        netloc = f"[{host}]" if ":" in host else host
        if port and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
            netloc += f":{port}"
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", "&".join(query), ""))
    except (ValueError, TypeError):
        return None


def domain_matches(url: str, domains: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def publishing_platform(url: str) -> str:
    """Derive display identity from the actual source, never a generated category label."""
    for name, domains in {
        "YouTube": ["youtube.com", "youtu.be"],
        "Vimeo": ["vimeo.com"],
        "Instagram": ["instagram.com"],
        "Pinterest": ["pinterest.com", "pinterest.co.uk"],
    }.items():
        if domain_matches(url, domains):
            return name
    return (urlsplit(url).hostname or "Unknown source").lower().removeprefix("www.")[:120]


@dataclass
class SearchJob:
    strand: str
    queries: list[str]
    categories: list[Source]
    domains: list[str]
    instructions: list[str]


class SourcePolicy:
    def __init__(self):
        self.sources = {
            name: definition.model_dump() for name, definition in load_source_registry().categories.items()
        }

    def jobs(self, strategy: SearchStrategy, limit: int, brief: StoredBrief | None = None) -> list[SearchJob]:
        selected = normalize_sources(strategy.preferred_sources)
        if not selected or any(category not in self.sources for category in selected):
            raise ValueError("Select at least one registered source category")
        # Reuse priorities from the existing planning call; legacy strategies get
        # a small deterministic fallback, without another model request.
        context = " ".join(
            [strategy.summary, *([brief.concept, brief.format, brief.purpose] if brief else [])]
        ).lower()
        keywords = {
            "video": r"\b(film|video|cinema|cinematic)\b",
            "awards": r"\b(award|awards|winning)\b",
            "editorial": r"\b(editorial|publication|publications|campaign)\b",
            "brand_films": r"\b(brand|corporate)\b",
            "original_work": r"\b(studio|director|production)\b",
            "interactive": r"\b(interactive|website|web|digital experience)\b",
            "podcast": r"\b(podcast|podcasts|interview|interviews|audio)\b",
            "social": r"\b(social|reel|reels|instagram|pinterest)\b",
        }
        fallback = sorted(
            selected,
            key=lambda category: (
                not bool(re.search(keywords[self.sources[category]["group"]], context)),
                self.sources[category]["group"] != "original_work",
            ),
        )
        ordered = list(
            dict.fromkeys(
                [*(category for category in strategy.source_priority if category in selected), *fallback]
            )
        )
        jobs = []
        seen = set()
        for index in range(limit):
            categories = [ordered[index % len(ordered)]]
            strand = strategy.strands[index % len(strategy.strands)]
            signature = (tuple(categories), tuple(strand.queries))
            if signature in seen:
                continue
            seen.add(signature)
            domains = list(
                dict.fromkeys(
                    domain for category in categories for domain in self.sources[category]["domains"]
                )
            )
            jobs.append(
                SearchJob(
                    strand=strand.name,
                    queries=strand.queries,
                    categories=categories,
                    domains=domains,
                    instructions=[self.sources[category]["instruction"] for category in categories],
                )
            )
        return jobs

    def category(
        self, url: str, suggested: Source, preferred: list[Source], discovered: list[Source] | None = None
    ) -> Source | None:
        selected = set(normalize_sources(preferred))
        allowed = selected & set(normalize_sources(discovered)) if discovered is not None else selected
        for category, policy in self.sources.items():
            if policy["domains"] and domain_matches(url, policy["domains"]):
                return category if category in allowed else None
        # Open-web categories are provisional: do not relabel a known platform as a studio.
        category = normalize_sources([suggested])[0]
        if category == "Studios & Production Companies" and category in allowed and canonical_url(url):
            return category
        return None
