"""Email recheck sources — discover a company email from multiple backends.

Each source implements :class:`BaseEmailSource` and returns an
:class:`EmailCandidate` when an email is found, or ``None``.

Sources:

* :class:`KboZipEmailSource` — extract email from the KBO bulk ZIP
  export (no network request).
* :class:`PappersEmailSource` — extract email from a pappers.be
  company page (network request).
"""
from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from urllib.parse import urlparse

try:
    import requests as requests  # noqa: F811 — module-level for patching
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

from reswip_leads.enrichment.pappers import (
    PAPPERS_BASE_URL,
    _parse_pappers_page,
    slugify,
)
from reswip_leads.verification.kbo.zip_reader import KboZipReader


logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

_SPA_INDICATORS = ("<noscript>", "window.location", '<div id="app">')

_CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/contactez-nous",
    "/contacteer-ons",
    "/nous-contacter",
    "/kontakt",
)

_CONTACT_LINK_RE = re.compile(
    r'href="([^"]*(?:contact|kontakt|nous-contacter|contacteer)[^"]*)"',
    re.IGNORECASE,
)

_REJECTED_LOCAL_PREFIXES = ("noreply", "no-reply", "donotreply")

_REJECTED_DOMAINS = frozenset({
    "example.com",
    "test.com",
    "localhost",
    "pappers.be",
    "kbopub.economie.fgov.be",
    "google.com",
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "instagram.com",
})


def _pw_playwright():
    """Lazy-import and return the ``playwright.sync_api`` module."""
    from playwright.sync_api import sync_playwright  # type: ignore[import-untyped]

    return sync_playwright


def _needs_playwright(html: str, content_length: int) -> bool:
    """Return True when the raw response likely needs JS rendering."""
    if content_length < 500:
        return True
    lower = html.lower()
    return any(ind in lower for ind in _SPA_INDICATORS)


def _is_valid_email(value: str, website_domain: str = "") -> bool:
    """Return True if *value* looks like a plausible business email.

    Rejects noreply/no-reply/donotreply prefixes, disposable/test
    domains, and social-media domains.  Accepts ``info@`` when the
    domain matches *website_domain*.
    """
    if not value or len(value) > 254:
        return False
    value = value.strip().lower()
    if _EMAIL_RE.fullmatch(value) is None:
        return False
    local, _, domain = value.rpartition("@")
    if domain in _REJECTED_DOMAINS:
        return False
    if any(local.startswith(prefix) for prefix in _REJECTED_LOCAL_PREFIXES):
        return False
    return True


@dataclass
class EmailCandidate:
    """An email discovered by an email source."""

    email: str
    source: str
    confidence: str  # "High" | "Medium" | "Low"
    source_url: str = ""


class BaseEmailSource(abc.ABC):
    """Abstract base for email discovery sources."""

    @abc.abstractmethod
    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        """Return an :class:`EmailCandidate` if an email was found."""


class KboZipEmailSource(BaseEmailSource):
    """Extract email from KBO ZIP bulk data (no network request)."""

    def __init__(self, kbo_reader: KboZipReader, zip_path: str):
        self._reader = kbo_reader
        self._zip_path = zip_path

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        digits = re.sub(r"\D", "", tva or "")
        if not digits:
            return None

        records = self._reader.build_index(self._zip_path, {digits})
        record = records.get(digits)
        if record is None or not record.email or not _is_valid_email(record.email):
            return None

        return EmailCandidate(
            email=record.email.strip(),
            source="kbo_zip",
            confidence="High",
        )


KBO_PUB_URL = "https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html?ondernemingsnummer={ent}"


class KboEmailSource(BaseEmailSource):
    """Extract email from KBO pub page (network request)."""

    def __init__(self, config: Any = None):
        self._config = config
        self._session: Any = None

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        digits = re.sub(r"\D", "", tva or "")
        if not digits:
            return None

        url = KBO_PUB_URL.format(ent=digits)

        try:
            import requests as _requests

            session = self._session
            if session is None:
                session = _requests.Session()
                if proxy:
                    session.proxies.update(proxy)
            response = session.get(url, timeout=15)
        except Exception:
            logger.debug("KboEmailSource: request failed for %s", url)
            return None

        status_code = getattr(response, "status_code", 0)
        if status_code != 200:
            return None

        html = getattr(response, "text", "")
        if not html or "Geen gegevens" in html:
            return None

        emails = _EMAIL_RE.findall(html)
        for email in emails:
            if _is_valid_email(email):
                return EmailCandidate(
                    email=email,
                    source="kbo",
                    confidence="Medium",
                    source_url=url,
                )

        return None


class PappersEmailSource(BaseEmailSource):
    """Extract email from Pappers company page (network request)."""

    def __init__(self, config: Any = None):
        self._config = config
        self._session: Any = None

    @staticmethod
    def _build_url(company_name: str, enterprise_number: str) -> str:
        slug = slugify(company_name) if company_name else ""
        if slug:
            return f"{PAPPERS_BASE_URL}/fr/company/{slug}-{enterprise_number}"
        return f"{PAPPERS_BASE_URL}/fr/company/{enterprise_number}"

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        digits = re.sub(r"\D", "", tva or "")
        if not digits:
            return None

        url = self._build_url(company_name, digits)

        try:
            import requests as _requests

            session = self._session
            if session is None:
                session = _requests.Session()
                if proxy:
                    session.proxies.update(proxy)
            response = session.get(url, timeout=15)
        except Exception:
            logger.debug("PappersEmailSource: request failed for %s", url)
            return None

        status_code = getattr(response, "status_code", 0)
        if status_code != 200:
            return None

        html = getattr(response, "text", "")
        parsed = _parse_pappers_page(html)

        for email in parsed.emails:
            if _is_valid_email(email):
                return EmailCandidate(
                    email=email,
                    source="pappers",
                    confidence="Low",
                )

        return None


class WebsiteEmailSource(BaseEmailSource):
    """Extract email by scraping the company website.

    Uses ``requests`` first; falls back to Playwright when the raw
    response looks like an SPA (< 500 bytes, <noscript>, window.location,
    or empty ``<div id="app">``).

    Checks the main page first, then common contact page paths
    (/contact, /contact-us, etc.) and any contact links found on the page.
    """

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        if not website_url:
            return None

        website_domain = urlparse(website_url).hostname or ""
        base_url = website_url.rstrip("/")

        # 1. Check main page
        html = self._fetch(website_url, proxy)
        if html:
            if _needs_playwright(html, len(html.encode("utf-8"))):
                rendered = self._fetch_with_playwright(website_url, proxy)
                if rendered:
                    html = rendered
            result = self._extract_email(html, website_domain, website_url)
            if result:
                return result

            # Find contact links on main page
            contact_links = self._find_contact_links(html, base_url)
        else:
            contact_links = []

        # 2. Try common contact paths
        for path in _CONTACT_PATHS:
            contact_url = f"{base_url}{path}"
            if contact_url in contact_links:
                continue  # will be tried below
            contact_links.append(contact_url)

        # 3. Try contact links
        for contact_url in contact_links[:5]:  # limit to 5 pages
            html = self._fetch(contact_url, proxy)
            if html is None:
                continue
            if _needs_playwright(html, len(html.encode("utf-8"))):
                rendered = self._fetch_with_playwright(contact_url, proxy)
                if rendered:
                    html = rendered
            result = self._extract_email(html, website_domain, contact_url)
            if result:
                return result

        return None

    def _extract_email(
        self, html: str, website_domain: str, source_url: str
    ) -> Optional[EmailCandidate]:
        """Extract a valid email from HTML content."""
        emails = _EMAIL_RE.findall(html)
        # Normalize domain: strip www. prefix for comparison
        normalized_domain = website_domain.lower()
        if normalized_domain.startswith("www."):
            normalized_domain = normalized_domain[4:]
        for email in emails:
            if not _is_valid_email(email):
                continue
            local, _, domain = email.rpartition("@")
            email_domain = domain.lower()
            if email_domain.startswith("www."):
                email_domain = email_domain[4:]
            if email_domain != normalized_domain:
                continue
            confidence = "Medium" if local.lower() == "info" else "Low"
            return EmailCandidate(
                email=email,
                source="website",
                confidence=confidence,
                source_url=source_url,
            )
        return None

    def _find_contact_links(self, html: str, base_url: str) -> list:
        """Find contact page links in HTML."""
        links = []
        seen = set()
        for match in _CONTACT_LINK_RE.finditer(html):
            href = match.group(1)
            if href.startswith("/"):
                href = base_url + href
            elif not href.startswith("http"):
                continue
            if href not in seen:
                seen.add(href)
                links.append(href)
        return links

    def _fetch(self, url: str, proxy: Optional[dict]) -> Optional[str]:
        if requests is None:
            return None
        try:
            session = requests.Session()
            if proxy:
                session.proxies.update(proxy)
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                return None
            return resp.text
        except Exception:
            logger.debug("WebsiteEmailSource: request failed for %s", url)
            return None

    def _fetch_with_playwright(
        self, url: str, proxy: Optional[dict]
    ) -> Optional[str]:
        try:
            with _pw_playwright() as pw:
                launch_args: dict = {}
                if proxy:
                    http_proxy = proxy.get("http") or proxy.get("https")
                    if http_proxy:
                        launch_args["proxy"] = {"server": http_proxy}
                browser = pw.chromium.launch(**launch_args)
                page = browser.new_context().new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html = page.content()
                browser.close()
                return html
        except Exception:
            logger.debug("WebsiteEmailSource: Playwright failed for %s", url)
            return None


__all__ = [
    "BaseEmailSource",
    "EmailCandidate",
    "KboZipEmailSource",
    "PappersEmailSource",
    "WebsiteEmailSource",
    "_is_valid_email",
]
