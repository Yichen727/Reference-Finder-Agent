"""Bounded public-page inspection with DNS pinning, robots checks and no credentials."""

import ipaddress
import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import HttpUrl

from backend.core.config import Settings
from backend.schemas.models import PageCheck
from backend.services.source_policy import canonical_url, public_address

AGENT = "AtomizedReferenceFinder"
USER_AGENT = f"{AGENT}/0.3 (public reference metadata research)"


class CheckStopped(Exception):
    def __init__(self, status: str, reason: str):
        self.status, self.reason = status, reason


class PublicHTML(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip = 0
        self.in_title = False
        self.in_h1 = False
        self.title_parts, self.heading_parts, self.parts = [], [], []
        self.meta = {}
        self.canonical = None
        self.media = []
        self.ld = []
        self.in_ld = False
        self.size = 0

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag in {"script", "style", "nav", "footer", "form", "noscript", "template"}:
            self.skip += 1
        if tag == "script" and data.get("type") == "application/ld+json":
            self.in_ld = True
        self.in_title = self.in_title or tag == "title"
        self.in_h1 = self.in_h1 or tag == "h1"
        if tag == "meta":
            self.meta[(data.get("property") or data.get("name") or "").lower()] = data.get("content", "")
        if tag == "link" and "canonical" in (data.get("rel") or "").lower().split():
            self.canonical = canonical_url(urljoin(self.base_url, data.get("href", "")))
        if tag == "iframe" and not self.skip:
            self.add_media(data.get("src", ""))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "form", "noscript", "template"}:
            self.skip = max(0, self.skip - 1)
        if tag == "script":
            self.in_ld = False
        if tag == "title":
            self.in_title = False
        if tag == "h1":
            self.in_h1 = False

    def handle_data(self, data):
        if self.in_ld:
            self.ld.append(data[:30000])
        if self.in_title:
            self.title_parts.append(data)
        if self.in_h1 and not self.skip:
            self.heading_parts.append(data)
        if not self.skip and self.size < 30000:
            self.parts.append(data)
            self.size += len(data)

    def add_media(self, value):
        url = canonical_url(urljoin(self.base_url, value))
        if url and (urlsplit(url).hostname or "") in {
            "www.youtube.com",
            "youtube.com",
            "www.youtube-nocookie.com",
            "youtube-nocookie.com",
            "youtu.be",
            "player.vimeo.com",
            "vimeo.com",
        }:
            if url not in self.media and len(self.media) < 12:
                self.media.append(url)

    def extract(self):
        for block in self.ld[:8]:
            try:
                data = json.loads(block)
            except (ValueError, RecursionError):
                continue
            objects = data if isinstance(data, list) else [data]
            for item in objects[:20]:
                if isinstance(item, dict) and item.get("@type") == "VideoObject":
                    self.add_media(str(item.get("embedUrl") or item.get("contentUrl") or ""))
        title = self.meta.get("og:title") or " ".join(self.title_parts) or " ".join(self.heading_parts)
        description = self.meta.get("og:description") or self.meta.get("description", "")
        text = " ".join([" ".join(self.heading_parts), description, " ".join(self.parts)])
        return re.sub(r"\s+", " ", title).strip()[:6000], re.sub(r"\s+", " ", text).strip()[:6000]


@dataclass
class Download:
    url: str
    status: int
    text: str


class PageVerifier:
    def __init__(self, settings: Settings, *, resolver=None, transport=None):
        self.settings = settings
        self.resolver = resolver or socket.getaddrinfo
        self.transport = transport
        self.deadline = 0.0
        self.requests = 0
        self.checked = 0
        self.lock = threading.Lock()
        self.origin_locks = {}
        self.robots = {}
        self.last_request = {}
        self.rate_limited = set()
        self.robot_failures = {}

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CheckStopped("unverified", "Page-check time budget reached.")
        return remaining

    def pinned_url(self, url: str):
        clean = canonical_url(url)
        if not clean:
            raise CheckStopped("blocked", "Unsafe or non-public URL.")
        parsed = urlsplit(clean)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in {80, 443}:
            raise CheckStopped("blocked", "Only public HTTP and HTTPS ports are allowed.")
        self.remaining()
        try:
            answers = self.resolver(host, port, type=socket.SOCK_STREAM)
            addresses = {ipaddress.ip_address(answer[4][0]) for answer in answers}
        except (OSError, ValueError):
            raise CheckStopped("unverified", "DNS resolution failed; page access is unknown.") from None
        self.remaining()
        if not addresses or any(not public_address(address) for address in addresses):
            raise CheckStopped("blocked", "DNS includes a private, local or reserved address.")
        address = sorted(addresses, key=lambda item: (item.version, str(item)))[0]
        target = httpx.URL(clean).copy_with(host=str(address))
        return clean, target, host, parsed.netloc

    def download(self, url: str, *, robots=False, before_hop=None) -> Download:
        current = url
        for hop in range(self.settings.verification_max_redirects + 1):
            clean, target, host, authority = self.pinned_url(current)
            if not robots and before_hop:
                before_hop(clean)
            with self.lock:
                if self.requests >= self.settings.verification_max_requests:
                    raise CheckStopped("unverified", "Page-request budget reached.")
                self.requests += 1
            headers = {
                "Host": authority,
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity",
                "Accept": "text/plain" if robots else "text/html,application/xhtml+xml,text/plain",
            }
            timeout = min(self.remaining(), self.settings.verification_timeout_seconds)
            # A fresh client per hop prevents cookie forwarding and cross-host TLS-pool reuse.
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=timeout, transport=self.transport
            ) as client:
                with client.stream(
                    "GET", target, headers=headers, extensions={"sni_hostname": host}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or hop == self.settings.verification_max_redirects:
                            raise CheckStopped("unverified", "Redirect limit reached or destination missing.")
                        current = urljoin(clean, location)
                        continue
                    status = response.status_code
                    if status == 429:
                        parsed = urlsplit(clean)
                        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
                        with self.lock:
                            self.rate_limited.add(origin)
                    if status in {401, 403, 429}:
                        return Download(clean, status, "")
                    if status >= 400:
                        return Download(clean, status, "")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    allowed = {"text/plain", "text/html", "application/xhtml+xml"}
                    if content_type not in allowed:
                        raise CheckStopped(
                            "unverified", "Unsupported content type; media was not downloaded."
                        )
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in {"", "identity"}:
                        raise CheckStopped("unverified", "Compressed response was not processed.")
                    size = response.headers.get("content-length", "")
                    if size.isdigit() and int(size) > self.settings.verification_max_bytes:
                        raise CheckStopped("unverified", "Page exceeds the download size limit.")
                    body = bytearray()
                    for chunk in response.iter_raw():
                        self.remaining()
                        body.extend(chunk)
                        if len(body) > self.settings.verification_max_bytes:
                            raise CheckStopped("unverified", "Page exceeds the download size limit.")
                    charset = response.charset_encoding or "utf-8"
                    try:
                        text = bytes(body).decode(charset, errors="replace")
                    except LookupError:
                        text = bytes(body).decode("utf-8", errors="replace")
                    return Download(clean, status, text)
        raise CheckStopped("unverified", "Redirect limit reached.")

    def permitted(self, url: str):
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        with self.lock:
            gate = self.origin_locks.setdefault(origin, threading.Lock())
        if not gate.acquire(timeout=self.remaining()):
            raise CheckStopped("unverified", "Origin request queue timed out.")
        try:
            with self.lock:
                limited = origin in self.rate_limited
            if limited:
                raise CheckStopped(
                    "restricted", "Origin is rate limited; further page requests were skipped."
                )
            if origin in self.robot_failures:
                status, reason = self.robot_failures[origin]
                raise CheckStopped(status, reason)
            if origin not in self.robots:
                downloaded = self.download(origin + "/robots.txt", robots=True)
                if downloaded.status == 404:
                    rules = RobotFileParser()
                    rules.parse([])
                elif 200 <= downloaded.status < 300:
                    rules = RobotFileParser()
                    rules.parse(downloaded.text.splitlines())
                else:
                    status = "restricted" if downloaded.status in {401, 403, 429} else "unverified"
                    reason = "robots.txt could not be checked; page was not fetched."
                    self.robot_failures[origin] = (status, reason)
                    raise CheckStopped(status, reason)
                self.robots[origin] = rules
                self.last_request[origin] = time.monotonic()
            rules = self.robots[origin]
            if not rules.can_fetch(AGENT, url):
                raise CheckStopped("restricted", "robots.txt disallows this page.")
            rate = rules.request_rate(AGENT)
            delay = max(
                0.25,
                rules.crawl_delay(AGENT) or 0,
                rate.seconds / rate.requests if rate and rate.requests else 0,
            )
            wait = delay - (time.monotonic() - self.last_request.get(origin, 0))
            if wait >= self.remaining():
                raise CheckStopped("unverified", "Origin crawl delay exceeds the remaining time budget.")
            if wait > 0:
                time.sleep(wait)
            self.last_request[origin] = time.monotonic()
        finally:
            gate.release()

    def check(self, url: str) -> PageCheck:
        with self.lock:
            self.checked += 1
        result = PageCheck(original_url=url)
        try:

            def before_hop(clean):
                if re.search(r"/(?:login|signin|sign-in|checkpoint)(?:/|\?|$)", urlsplit(clean).path, re.I):
                    raise CheckStopped("restricted", "Destination is a login or access-check page.")
                self.permitted(clean)

            downloaded = self.download(url, before_hop=before_hop)
            result.final_url = HttpUrl(downloaded.url)
            result.http_status = downloaded.status
            if downloaded.status in {401, 403, 429}:
                raise CheckStopped("restricted", "Access is restricted or rate limited.")
            if not 200 <= downloaded.status < 300:
                raise CheckStopped("unreachable", f"Source returned HTTP {downloaded.status}.")
            parser = PublicHTML(downloaded.url)
            parser.feed(downloaded.text)
            result.title, result.text = parser.extract()
            if re.search(
                r"^(?:log in|login|sign in|access denied|just a moment|verify you are human|"
                r"security check|before you continue)",
                result.title,
                re.I,
            ):
                raise CheckStopped("restricted", "Page is a login wall or browser challenge.")
            if re.match(r"^(?:page not found|404\b|not found\b)", result.title, re.I):
                raise CheckStopped("unreachable", "Source appears to be a soft-404 page.")
            if len(result.text) < 1500 and re.search(
                r"(?:enable javascript and cookies|verify you are human|sign in to continue|"
                r"subscribe to continue|this video is private|video is unavailable|content isn.t available)",
                result.text,
                re.I,
            ):
                raise CheckStopped("restricted", "Page contains an access wall or browser challenge.")
            if len(result.text) < 100:
                raise CheckStopped(
                    "unverified", "Page has insufficient readable text; it may require JavaScript."
                )
            path = urlsplit(downloaded.url).path.rstrip("/").lower()
            generic = path in {
                "",
                "/work",
                "/works",
                "/projects",
                "/search",
                "/videos",
                "/channel",
                "/portfolio",
            }
            generic = generic or path.startswith(("/results", "/search/", "/channel/", "/@"))
            generic = generic or bool(
                re.fullmatch(
                    r"(?:home|work|works|projects|search|portfolio|youtube|vimeo|instagram|pinterest)"
                    r"(?:\s*[|\-].*)?",
                    result.title,
                    re.I,
                )
            )
            result.work_page = not generic
            canonical = parser.canonical
            if canonical and urlsplit(canonical).hostname == urlsplit(downloaded.url).hostname:
                if urlsplit(canonical).path.rstrip("/").lower() not in {
                    "",
                    "/work",
                    "/works",
                    "/projects",
                    "/portfolio",
                    "/search",
                    "/videos",
                }:
                    result.canonical_url = HttpUrl(canonical)
            result.media_urls = [HttpUrl(url) for url in parser.media]
            result.status = "accessible"
            result.reason = (
                "Readable public page checked; this does not establish video analysis or reproduction rights."
            )
        except CheckStopped as error:
            result.status, result.reason = error.status, error.reason
            if result.status != "accessible":
                result.text = ""
        except (httpx.HTTPError, OSError):
            result.status, result.reason = (
                "unverified",
                "Page request failed or timed out; access is unknown.",
            )
        return result

    def check_many(self, urls: list[str], *, deadline: float | None = None):
        self.deadline = min(
            deadline or float("inf"), time.monotonic() + self.settings.verification_total_seconds
        )
        selected = list(dict.fromkeys(urls))[: self.settings.verification_max_pages]
        results = {}
        pool = ThreadPoolExecutor(max_workers=self.settings.verification_workers)
        futures = {pool.submit(self.check, url): url for url in selected}
        try:
            for future in as_completed(futures, timeout=max(0.01, self.deadline - time.monotonic())):
                url = futures[future]
                results[url] = future.result()
        except TimeoutError:
            pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for url in urls:
            results.setdefault(
                url, PageCheck(original_url=url, reason="Page-check count or time budget reached.")
            )
        return results
