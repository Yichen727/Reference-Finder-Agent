"""Bounded preview HTTP with separate robots policy and resource accessibility."""

import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from backend.services.page_verification import AGENT, USER_AGENT, CheckStopped, PageVerifier


def content_type(value):
    return re.split(r"[;,]", value or "", maxsplit=1)[0].strip().lower()


@dataclass
class RobotsPolicy:
    rules: RobotFileParser | None
    status: int
    expires: float
    reason: str = ""


class PreviewAccess:
    """Shared only by this preview service, never by the research verifier."""

    def __init__(self):
        self.lock = threading.Lock()
        self.policies, self.gates, self.last_request = {}, {}, {}

    def gate(self, origin):
        with self.lock:
            if len(self.gates) >= 512 and origin not in self.gates:
                expired = next((key for key, gate in self.gates.items() if not gate.locked()
                                and self.policies.get(key) is not None
                                and self.policies[key].expires <= time.monotonic()), None)
                if expired is None:
                    raise CheckStopped("unverified", "Preview origin capacity reached.")
                self.gates.pop(expired, None)
                self.policies.pop(expired, None)
                self.last_request.pop(expired, None)
            return self.gates.setdefault(origin, threading.Lock())


class PreviewFetcher(PageVerifier):
    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        self.deadline = time.monotonic() + 24
        self.access = PreviewAccess()
        self.diagnostics = []

    def _read(self, url, limit):
        clean, target, host, authority = self.pinned_url(url)
        if re.search(r"/(?:login|signin|sign-in|checkpoint)(?:/|$)", urlsplit(clean).path, re.I):
            raise CheckStopped("restricted", "Preview destination requires authentication.")
        if self.requests >= 16:
            raise CheckStopped("unverified", "Preview request budget reached.")
        self.requests += 1
        with httpx.Client(trust_env=False, follow_redirects=False, transport=self.transport,
                          timeout=min(4, self.remaining())) as client:
            with client.stream("GET", target, extensions={"sni_hostname": host}, headers={
                "Host": authority, "User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                "Accept": "text/html,application/json,image/*,text/plain;q=0.8,*/*;q=0.1",
            }) as response:
                status = response.status_code
                mime = content_type(response.headers.get("content-type"))
                if status != 200:
                    return clean, mime, b"", status, response.headers.get("location")
                size = response.headers.get("content-length", "")
                if size.isdigit() and int(size) > limit:
                    raise CheckStopped("unverified", "Preview is too large.")
                body = bytearray()
                # httpx decodes supported content encodings. Bound the decoded
                # bytes as well as Content-Length; compressed pages cannot expand unboundedly.
                for chunk in response.iter_bytes(chunk_size=65536):
                    self.remaining()
                    body.extend(chunk)
                    if len(body) > limit:
                        raise CheckStopped("unverified", "Preview is too large.")
                return clean, mime, bytes(body), status, None

    def _robots(self, origin):
        current = origin + "/robots.txt"
        try:
            for _ in range(4):
                _, mime, body, status, location = self._read(current, 128_000)
                if status in {301, 302, 303, 307, 308} and location:
                    current = urljoin(current, location)
                    continue
                rules = RobotFileParser()
                if status == 404:
                    rules.parse([])
                elif status == 200:
                    text = body.decode("utf-8", errors="replace")
                    if "\x00" in text or re.search(r"<\s*(?:html|form|script|!doctype)", text, re.I):
                        break
                    if not re.search(r"(?im)^\s*(?:user-agent|allow|disallow)\s*:", text):
                        break
                    rules.parse(text.splitlines())
                else:
                    return RobotsPolicy(None, status, time.monotonic() + 120,
                                        f"robots.txt returned HTTP {status}.")
                return RobotsPolicy(rules, status, time.monotonic() + 3600)
        except (CheckStopped, httpx.HTTPError, OSError) as error:
            return RobotsPolicy(None, 0, time.monotonic() + 120,
                                getattr(error, "reason", "robots.txt could not be reached."))
        return RobotsPolicy(None, 0, time.monotonic() + 120, "robots.txt is not a readable policy.")

    def permitted(self, url, *, resource=False):
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        gate = self.access.gate(origin)
        if not gate.acquire(timeout=self.remaining()):
            raise CheckStopped("unverified", "Preview origin queue timed out.")
        try:
            policy = self.access.policies.get(origin)
            if policy is None or policy.expires <= time.monotonic():
                policy = self._robots(origin)
                self.access.policies[origin] = policy
            if policy.rules is None:
                self.diagnostics.append({"stage": "robots", "url": origin,
                                         "code": "unknown_policy", "reason": policy.reason})
                if not resource or policy.status in {401, 429}:
                    raise CheckStopped("restricted", "robots.txt could not be checked; page was not fetched.")
                # Unknown policy is not an image access decision. Only an exact
                # source-declared public asset is probed; its actual HTTP denial still stops it.
                delay = 0.25
            else:
                if not policy.rules.can_fetch(AGENT, url):
                    raise CheckStopped("restricted", "robots.txt disallows this resource.")
                rate = policy.rules.request_rate(AGENT)
                delay = max(0.25, policy.rules.crawl_delay(AGENT) or 0,
                            rate.seconds / rate.requests if rate and rate.requests else 0)
            last = self.access.last_request.get(origin)
            wait = max(0, delay - (time.monotonic() - last)) if last is not None else 0
            if wait >= self.remaining():
                raise CheckStopped("unverified", "Origin crawl delay exceeds the preview budget.")
            if wait:
                self.diagnostics.append({"stage": "queue", "url": origin, "wait_seconds": round(wait, 2)})
                time.sleep(wait)
            self.access.last_request[origin] = time.monotonic()
        finally:
            gate.release()

    def fetch(self, url, types, limit):
        image = any(value.startswith("image/") for value in types)
        resource = image or "application/json" in types
        current = url
        for _ in range(4):
            clean, *_ = self.pinned_url(current)
            self.permitted(clean, resource=resource)
            final, mime, body, status, location = self._read(clean, limit)
            if status in {301, 302, 303, 307, 308} and location:
                current = urljoin(final, location)
                continue
            if status != 200:
                if status == 429:
                    parts = urlsplit(final)
                    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
                    self.access.policies[origin] = RobotsPolicy(None, 429, time.monotonic() + 120)
                raise CheckStopped("restricted", f"Preview source returned HTTP {status}.")
            # Images are validated by their raster contents in Pillow, not by
            # possibly incorrect MIME labels. HTML/JSON still require their expected types.
            if not image and mime not in types:
                raise CheckStopped("unverified", "Unsupported preview response.")
            return final, mime, body
        raise CheckStopped("unverified", "Preview redirect limit reached.")
