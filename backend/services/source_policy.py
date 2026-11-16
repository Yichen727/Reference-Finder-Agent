import ipaddress
import json
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from pydantic import HttpUrl, TypeAdapter

from backend.core.config import ROOT
from backend.schemas.models import SearchStrategy, Source

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


@dataclass
class SearchJob:
    strand: str
    queries: list[str]
    categories: list[Source]
    domains: list[str]
    instructions: list[str]


class SourcePolicy:
    def __init__(self):
        self.sources = json.loads((ROOT / "config/sources.json").read_text(encoding="utf-8"))

    def jobs(self, strategy: SearchStrategy, limit: int) -> list[SearchJob]:
        groups: dict[str, list[Source]] = {}
        for category in strategy.preferred_sources:
            groups.setdefault(self.sources[category]["group"], []).append(category)
        # Prioritise original work and video, then official awards and restricted social sources.
        ordered = [
            groups[group] for group in ["original_work", "video", "awards", "social"] if group in groups
        ]
        jobs = []
        seen = set()
        for index in range(limit):
            categories = ordered[index % len(ordered)]
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

    def category(self, url: str, suggested: Source, preferred: list[Source]) -> Source | None:
        for category, policy in self.sources.items():
            if policy["domains"] and domain_matches(url, policy["domains"]):
                return category if category in preferred else None
        # Open-web categories are provisional: do not relabel a known platform as a studio.
        if suggested in {"Studio portfolios", "Production companies"} and suggested in preferred:
            return suggested
        return None
