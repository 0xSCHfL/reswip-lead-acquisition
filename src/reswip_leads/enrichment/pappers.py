"""Pappers.be public contact enrichment.

Scrapes pappers.be for a company's directors, public contact email,
phone, and website. The lookup is keyed on the digits-only TVA with
the company name as a slug for URL construction. If the company name
is not provided, the URL falls back to the digits-only TVA only.

The scraping logic is adapted from the proven insurance-project
helpers (``scripts/pappers/enrich.py`` and ``find_emails.py``):

- ``slugify(name)`` — same regex.
- ``decode_cf_email(encoded)`` — Cloudflare email-protection decoder.
- The ``/fr/search-officers?q=...`` regex for director extraction.
- The phone-number regex for Belgian numbers.
- The href filter that excludes pappers/google/facebook links for
  website extraction.

Broker-specific logic (parallel workers, proxy rotator file loading,
the `--workers` / `--sleep` CLI knobs) is **not** carried over. The
adapter is single-threaded and takes its proxy through
:class:`EnrichmentConfig` instead. This keeps it sector-neutral and
trivial to mock in tests.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from reswip_leads.enrichment.base import (
    BaseEnricher,
    EnrichmentConfig,
    EnrichmentResult,
    EnrichmentStatus,
    Evidence,
    confidence_for,
    digits_only,
)


# ── Public constants (used by tests) ───────────────────────────────

PAPPERS_BASE_URL = "https://www.pappers.be"
PAPPERS_COMPANY_URL = f"{PAPPERS_BASE_URL}/fr/company/{{slug}}-{{ent}}"

# ── Parsing helpers ────────────────────────────────────────────────

_SLUG_STRIP = re.compile(r"[^a-z0-9\s-]")
_SLUG_COLLAPSE = re.compile(r"[\s]+")
_DIRECTOR_RE = re.compile(r"/fr/search-officers\?q=([^\"]+)")
_DIRECTOR_CONTEXT_RE = re.compile(
    r'href="/fr/search-officers\?q=([^\"]+)"[^>]*>.*?</a>\s*(?:—|-)\s*([^<\n]+)',
    re.IGNORECASE | re.DOTALL,
)
# Pappers sometimes exposes the mandate/function label through the same
# officer-search URL used for a person's name. These must never become a
# contact's first name.
_NON_PERSON_FIRST_NAMES = {
    "administrateur",
    "administratrice",
    "bestuurder",
    "directeur",
    "directrice",
    "fondateur",
    "fondatrice",
    "gérant",
    "gérante",
    "manager",
    "président",
    "présidente",
    "zaakvoerder",
}
_CF_EMAIL_RE = re.compile(r"/cdn-cgi/l/email-protection#([a-f0-9]+)")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+32|0032|0)[\s.\-]?\d{1,3}[\s.\-]?\d{2,3}[\s.\-]?\d{2,3}[\s.\-]?\d{2,3}"
)
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')

# Domains to exclude when extracting the company website from page links.
_EXCLUDED_WEBSITE_DOMAINS = (
    "pappers",
    "google",
    "facebook",
    "twitter",
    "linkedin",
    "ejustice.just.fgov.be",
    "economie.fgov.be",
    "belgium.be",
    "kruispuntbank.be",
)


def slugify(name: str) -> str:
    """Convert ``name`` into a pappers.be URL slug.

    The algorithm matches the one used in the insurance-project
    reference: lowercase, strip non-alphanumerics, collapse whitespace
    into single hyphens, trim leading/trailing hyphens.
    """
    s = (name or "").lower().strip()
    s = _SLUG_STRIP.sub("", s)
    s = _SLUG_COLLAPSE.sub("-", s)
    return s.strip("-")


def decode_cf_email(encoded: str) -> str:
    """Decode a Cloudflare ``data-cfemail`` ciphertext.

    Mirrors the algorithm in
    ``scripts/pappers/find_emails.py:decode_cf_email``. Returns an
    empty string if the input is malformed.
    """
    if not encoded or len(encoded) < 2:
        return ""
    try:
        key = int(encoded[:2], 16)
    except ValueError:
        return ""
    out: List[str] = []
    for i in range(2, len(encoded), 2):
        chunk = encoded[i : i + 2]
        if len(chunk) < 2:
            break
        try:
            out.append(chr(int(chunk, 16) ^ key))
        except ValueError:
            return ""
    return "".join(out)


# ── Parsed page (intermediate, dataclass for clarity) ──────────────


@dataclass
class _ParsedPappersPage:
    directors: List[Tuple[str, str]]
    positions: Dict[Tuple[str, str], str]
    emails: List[str]
    phones: List[str]
    websites: List[str]


def _parse_pappers_page(html: str) -> _ParsedPappersPage:
    """Extract directors, emails, phones, and websites from a
    pappers.be company page.

    The page format is HTML rendered server-side; we use the same
    regex-based approach as the reference scripts. We do **not**
    depend on the DOM structure because Pappers does not publish a
    stable schema for it.
    """
    if not html:
        return _ParsedPappersPage([], {}, [], [], [])

    # Directors — first/last from the search-officers URL.
    directors: List[Tuple[str, str]] = []
    positions: Dict[Tuple[str, str], str] = {}
    for context_match in _DIRECTOR_CONTEXT_RE.finditer(html):
        encoded = unquote(context_match.group(1))
        parts = encoded.split("+")
        if len(parts) < 2:
            continue
        first = parts[0].strip()
        last = " ".join(p.strip() for p in parts[1:] if p.strip()).strip()
        key = (first, last)
        position = " ".join(context_match.group(2).split())
        if first and last and position and first.casefold() not in _NON_PERSON_FIRST_NAMES:
            positions[key] = position
    seen_directors: set = set()
    for match in _DIRECTOR_RE.finditer(html):
        encoded = unquote(match.group(1))
        parts = encoded.split("+")
        if len(parts) < 2:
            continue
        first = parts[0].strip()
        last = " ".join(p.strip() for p in parts[1:] if p.strip()).strip()
        if not first or not last:
            continue
        if first.casefold() in _NON_PERSON_FIRST_NAMES:
            continue
        key = (first.lower(), last.lower())
        if key in seen_directors:
            continue
        seen_directors.add(key)
        directors.append((first, last))

    # Emails — Cloudflare-protected first, then raw occurrences.
    emails: List[str] = []
    seen_emails: set = set()
    for cf_match in _CF_EMAIL_RE.finditer(html):
        decoded = decode_cf_email(cf_match.group(1))
        if not decoded or "pappers" in decoded.lower():
            continue
        key = decoded.lower()
        if key not in seen_emails:
            seen_emails.add(key)
            emails.append(decoded)
    for raw_match in _EMAIL_RE.finditer(html):
        candidate = raw_match.group(0)
        if "pappers" in candidate.lower():
            continue
        key = candidate.lower()
        if key not in seen_emails:
            seen_emails.add(key)
            emails.append(candidate)

    # Phones — Belgian-format numbers, length-validated.
    phones: List[str] = []
    seen_phones: set = set()
    for ph_match in _PHONE_RE.finditer(html):
        candidate = ph_match.group(0)
        digits = "".join(ch for ch in candidate if ch.isdigit())
        if len(digits) < 8:
            continue
        key = digits
        if key in seen_phones:
            continue
        seen_phones.add(key)
        phones.append(candidate.strip())

    # Websites — first non-external link that survives the domain filter.
    websites: List[str] = []
    seen_websites: set = set()
    for href_match in _HREF_RE.finditer(html):
        href = href_match.group(1)
        low = href.lower()
        if any(domain in low for domain in _EXCLUDED_WEBSITE_DOMAINS):
            continue
        if href in seen_websites:
            continue
        seen_websites.add(href)
        websites.append(href)

    return _ParsedPappersPage(
        directors=directors,
        positions=positions,
        emails=emails,
        phones=phones,
        websites=websites,
    )


# ── Adapter ────────────────────────────────────────────────────────


class PappersEnricher(BaseEnricher):
    """Pappers.be contact enrichment.

    Sector-neutral. Looks up a company by TVA (digits-only) with the
    company name as a URL slug. Returns a structured
    :class:`EnrichmentResult` carrying the first director's first/last
    name, a public email, a phone, and a website link.
    """

    SOURCE_NAME = "pappers"

    def enrich(
        self, tva: str, company_name: str = ""
    ) -> Dict[str, Any]:
        """Look up ``tva`` (and optionally ``company_name``) on pappers.be.

        Never raises — failures are reported as
        :attr:`EnrichmentStatus.ERROR` with ``error`` set in the
        returned dict.
        """
        ent = digits_only(tva)
        if not ent:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                lookup_key=tva or "",
                error="empty or invalid TVA",
            ).to_dict()

        url = self.build_url(company_name, ent)
        lookup_key = ent if not company_name else f"{ent}|{company_name}"

        try:
            response = self._request(url)
        except Exception as exc:  # noqa: BLE001
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                lookup_key=lookup_key,
                source_url=url,
                error=f"{type(exc).__name__}: {exc}",
            ).to_dict()

        status_code = getattr(response, "status_code", 0)
        if status_code != 200:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                lookup_key=lookup_key,
                source_url=url,
                error=f"HTTP {status_code}",
            ).to_dict()

        html = getattr(response, "text", "")
        parsed = _parse_pappers_page(html)
        return self._build_result(parsed, lookup_key=lookup_key, source_url=url).to_dict()

    # ── Public helpers (used by tests) ──────────────────────────

    @staticmethod
    def build_url(company_name: str, enterprise_number: str) -> str:
        """Build the pappers.be company URL.

        Public so tests can assert the URL pattern without going
        through the full enrich() call.
        """
        slug = slugify(company_name) if company_name else ""
        ent = digits_only(enterprise_number)
        if slug:
            return PAPPERS_COMPANY_URL.format(slug=slug, ent=ent)
        return f"{PAPPERS_BASE_URL}/fr/company/{ent}"

    # ── Internal ────────────────────────────────────────────────

    def _build_result(
        self,
        parsed: _ParsedPappersPage,
        *,
        lookup_key: str,
        source_url: str,
    ) -> EnrichmentResult:
        """Convert a parsed page into an :class:`EnrichmentResult` with
        evidence for every field that has a value.

        Directors are only used when both first and last names are
        present and non-empty. We never invent a director — if the
        page has no first/last pair, ``first_name``/``last_name`` are
        left empty and no evidence is emitted.
        """
        fields: Dict[str, str] = {}
        evidence: List[Evidence] = []

        if parsed.directors:
            first, last = parsed.directors[0]
            if first and last:
                fields["first_name"] = first
                fields["last_name"] = last
                evidence.append(
                    Evidence(
                        source=self.SOURCE_NAME,
                        source_url=source_url,
                        field="first_name",
                        confidence=confidence_for("first_name"),
                        note="Person linked to the company TVA",
                    )
                )
                position = parsed.positions.get((first, last), "")
                if position:
                    fields["position"] = position
                    evidence.append(
                        Evidence(
                            source=self.SOURCE_NAME,
                            source_url=source_url,
                            field="position",
                            confidence=confidence_for("position"),
                            note="Function shown next to the Pappers officer",
                        )
                    )
                evidence.append(
                    Evidence(
                        source=self.SOURCE_NAME,
                        source_url=source_url,
                        field="last_name",
                        confidence=confidence_for("last_name"),
                        note="Person linked to the company TVA",
                    )
                )

        if parsed.emails:
            email = parsed.emails[0]
            fields["email"] = email
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="email",
                    confidence=confidence_for("email"),
                    note="Public email discovered on the company page",
                )
            )

        if parsed.phones:
            fields["phone"] = parsed.phones[0]
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="phone",
                    confidence=confidence_for("phone"),
                    note="Belgian-format phone found on the company page",
                )
            )

        if parsed.websites:
            fields["website"] = parsed.websites[0]
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="website",
                    confidence=confidence_for("website"),
                    note="First external link on the company page",
                )
            )

        # Always include the directors list in the flat dict for
        # backwards compatibility with the original PappersEnricher
        # contract (it exposed ``directors`` / ``emails`` / ``phones``).
        if parsed.directors:
            fields["directors"] = parsed.directors  # type: ignore[assignment]
        if parsed.emails:
            fields["emails"] = parsed.emails  # type: ignore[assignment]
        if parsed.phones:
            fields["phones"] = parsed.phones  # type: ignore[assignment]
        if parsed.websites:
            fields["websites"] = parsed.websites  # type: ignore[assignment]

        if not fields:
            return EnrichmentResult(
                status=EnrichmentStatus.NO_MATCH,
                lookup_key=lookup_key,
                source_url=source_url,
            )

        return EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields=fields,
            evidence=evidence,
            lookup_key=lookup_key,
            source_url=source_url,
        )


# ── Backward-compatible legacy stub ───────────────────────────────
#
# The original PappersEnricher.enrich() returned a dict with at
# minimum ``directors``, ``emails``, ``phones``, ``website``,
# ``enterprise_number``. The pipeline's tests assert that enrich(tva,
# name) returns a dict; the new PappersEnricher satisfies that via
# EnrichmentResult.to_dict(). This comment is here so the change is
# visible to anyone grepping for the legacy shape.


__all__ = ["PappersEnricher", "PAPPERS_BASE_URL", "PAPPERS_COMPANY_URL", "slugify"]
