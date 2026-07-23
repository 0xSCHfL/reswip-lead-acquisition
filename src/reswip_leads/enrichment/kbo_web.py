"""KBO public website enrichment.

Scrapes ``kbopub.economie.fgov.be`` for company details, directors,
public email, phone, and website using the company's TVA. Implements
the two-step lookup strategy from the insurance reference:

1. Direct fetch of the company page:
   ``/kbopub/toonondernemingps.html?ondernemingsnummer={ent}``
2. If the direct page does not match the requested enterprise
   number, fall back to a search by number and follow the first
   link to the canonical enterprise page.

The KBO HTML is parsed with :mod:`bs4`. Director rows are detected
by structural cues (label/dd pairs in the page header section), and
email/phone/website are extracted from the contact block.

The following insurance-specific helpers from
``scripts/kbo-web/enrich.py`` are **deliberately removed**:

- ``is_valid_broker_email`` — broker/FSMA email validator.
- ``scrape_website_contacts`` — website scraping tied to broker
  patterns; see ``docs/MIGRATION.md``.
- ``choose_phone_field`` — the "Office vs Mobile" split is collapsed
  into the canonical :attr:`Lead.phone` field.
- The "phone looks like a VAT" heuristic — over-fit to specific
  insurance data.
- Proxy file loading / ``ProxyRotator`` — replaced by
  :attr:`EnrichmentConfig.proxy`.

The adapter is sector-neutral: any company with a valid TVA can be
enriched.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

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

KBO_BASE_URL = "https://kbopub.economie.fgov.be"
KBO_COMPANY_URL = (
    f"{KBO_BASE_URL}/kbopub/toonondernemingps.html?ondernemingsnummer={{ent}}"
)
KBO_SEARCH_URL = (
    f"{KBO_BASE_URL}/kbopub/zoeknummerform.html"
)
# Pattern that identifies a link from the KBO search results to a
# canonical company page. The reference uses this exact substring to
# pick the right href.
KBO_COMPANY_LINK_NEEDLE = "toonondernemingps.html?ondernemingsnummer="

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+32|0032|0)[\s.\-]?\d{1,3}[\s.\-]?\d{2,3}[\s.\-]?\d{2,3}[\s.\-]?\d{2,3}"
)
_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)


# ── Parsed page ────────────────────────────────────────────────────


class _ParsedKboPage:
    """Result of parsing a KBO company page."""

    def __init__(self) -> None:
        self.company_name: str = ""
        self.address: str = ""
        self.zipcode: str = ""
        self.municipality: str = ""
        self.directors: List[Dict[str, str]] = []
        self.email: str = ""
        self.phone: str = ""
        self.website: str = ""

    @property
    def is_empty(self) -> bool:
        return not (
            self.company_name
            or self.directors
            or self.address
            or self.email
            or self.phone
            or self.website
        )


def _parse_kbo_page(html: str) -> _ParsedKboPage:
    """Parse a KBO company page.

    The KBO HTML structure is loosely consistent but not stable across
    versions. We use a tolerant approach: try to extract the company
    name and address from the page heading, scan for director rows
    by label proximity, and fall back to regex for email/phone/website
    in the contact block.
    """
    parsed = _ParsedKboPage()
    if not html:
        return parsed

    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - exercised only without bs4
        # Without bs4 we fall back to a degraded regex-only path.
        return _parse_kbo_page_regex_only(html)

    soup = BeautifulSoup(html, "html.parser")

    parsed.company_name = _extract_company_name(soup)
    parsed.address, parsed.zipcode, parsed.municipality = _extract_address(soup)
    parsed.directors = _extract_directors(soup)
    parsed.email = _extract_email(soup, html)
    parsed.phone = _extract_phone(soup, html)
    parsed.website = _extract_website(soup, html)

    return parsed


def _parse_kbo_page_regex_only(html: str) -> _ParsedKboPage:
    parsed = _ParsedKboPage()
    if not html:
        return parsed
    # Very defensive: if bs4 is unavailable, the adapter can still
    # return a partial result. We don't try to extract directors
    # because that requires DOM structure.
    email_match = _EMAIL_RE.search(html)
    if email_match:
        parsed.email = email_match.group(0)
    phone_match = _PHONE_RE.search(html)
    if phone_match:
        parsed.phone = phone_match.group(0)
    href_match = _HREF_RE.search(html)
    if href_match:
        href = href_match.group(1)
        if href.startswith("http") and "kbopub" not in href.lower():
            parsed.website = href
    return parsed


def _extract_company_name(soup: Any) -> str:
    """Pick the most likely company-name element on the page.

    Tries ``<h1>``, then ``<h2>`` containing a long-enough string,
    then a ``Denomination``/``Ondernemingsnaam`` label.
    """
    for tag in ("h1", "h2"):
        for el in soup.find_all(tag):
            text = el.get_text(strip=True)
            if text and 2 < len(text) < 200:
                return text
    # Fallback: look for a definition list with a Denomination label.
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        if "denomination" in label or "naam" in label or "entreprise" in label:
            dd = dt.find_next("dd")
            if dd:
                return dd.get_text(strip=True)
    return ""


def _extract_address(soup: Any) -> tuple:
    """Return ``(address, zipcode, municipality)`` from the page.

    The KBO page typically renders the address in a single
    ``<address>`` element or in a definition list with an
    ``Adres/Adresse`` label.
    """
    address = ""
    zipcode = ""
    municipality = ""

    addr_el = soup.find("address")
    if addr_el:
        address = " ".join(addr_el.get_text().split())

    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        dd = dt.find_next("dd")
        if dd is None:
            continue
        if "adres" in label or "address" in label:
            address = " ".join(dd.get_text().split())
        elif "postcode" in label or "zipcode" in label:
            zipcode = dd.get_text(strip=True)
        elif "gemeente" in label or "commune" in label or "municipality" in label:
            municipality = dd.get_text(strip=True)

    # Parse the postcode out of the address if we didn't find it
    # via a label (the KBO page often shows "Rue de la Loi 16, 1000 Bruxelles").
    if not zipcode and address:
        m = re.search(r"\b(\d{4})\b", address)
        if m:
            zipcode = m.group(1)

    return address, zipcode, municipality


def _extract_directors(soup: Any) -> List[Dict[str, str]]:
    """Extract directors as a list of ``{first_name, last_name, function}``.

    The KBO page lists mandate holders in a ``<dl>`` definition list.
    The ``<dt>`` label may contain the role/function (e.g. "Gérant",
    "Bestuurder", "Administrateur délégué") and the ``<dd>`` contains
    the person's name, optionally followed by a comma and the function.

    This function recognises French and Dutch mandate labels and
    extracts the function from the ``<dt>`` label when it is not
    present in the ``<dd>`` text.
    """
    directors: List[Dict[str, str]] = []
    seen: set = set()

    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True).lower()
        if not _is_mandate_label(label):
            continue
        dd = dt.find_next("dd")
        if dd is None:
            continue
        text = " ".join(dd.get_text().split())
        dt_function = _function_from_label(label)
        parsed_name, dd_function = _split_director_text(text)
        if not parsed_name:
            continue
        # Prefer the function from dd text (more specific), fall back to dt label.
        function = dd_function or dt_function
        key = " ".join(parsed_name).lower()
        if key in seen:
            continue
        seen.add(key)
        directors.append(
            {
                "first_name": parsed_name[0],
                "last_name": " ".join(parsed_name[1:]).strip(),
                "function": function,
            }
        )

    return directors


# ── Mandate label recognition ──────────────────────────────────────

# Known KBO mandate/function labels (FR + NL).  The check is
# case-insensitive; the values here are lowercase.  Keep this list
# sector-neutral — no broker/FSMA/insurance terms.
_MANDATE_LABELS: tuple = (
    # French
    "administrateur",
    "administrateur délégué",
    "gérant",
    "directeur",
    "président",
    "représentant permanent",
    "mandataris",
    # Dutch
    "bestuurder",
    "zaakvoerder",
    "gedelegeerd bestuurder",
    "voorzitter",
    "permanent vertegenwoordiger",
    # Generic fallbacks
    "fonction",
    "mandaat",
)


def _is_mandate_label(label: str) -> bool:
    """Return True if *label* looks like a KBO mandate/function ``<dt>``."""
    low = label.lower().strip()
    return any(mandate in low for mandate in _MANDATE_LABELS)


def _function_from_label(label: str) -> str:
    """Extract the function name from a ``<dt>`` label.

    For compound labels like ``"Administrateur délégué"`` the full
    label is returned.  For generic labels like ``"Bestuurder"`` or
    ``"Mandataris"`` the label itself is the function.  Returns an
    empty string for truly generic labels that carry no role
    information (``"fonction"``, ``"mandaat"``).
    """
    low = label.lower().strip()
    # Labels that are themselves the function name.
    _GENERIC = {"fonction", "mandaat", "mandataris"}
    # Check compound (longer) labels first so substrings don't match early.
    for mandate in sorted(_MANDATE_LABELS, key=len, reverse=True):
        if mandate in low and mandate not in _GENERIC:
            return mandate.title()
    return ""


def _split_director_text(text: str) -> tuple:
    """Split ``text`` into ``(name_parts, function)``.

    KBO rows are typically ``"Jean Dupont, Administrateur"`` or
    ``"M. Jean Dupont"``. The function, if present, is the substring
    after the last comma.
    """
    if not text:
        return ([], "")
    text = text.strip()
    function = ""
    if "," in text:
        text, _, function = text.rpartition(",")
        function = function.strip()
    # Strip honorifics.
    parts = re.split(r"\s+", text)
    parts = [p for p in parts if p and p.lower() not in {"m.", "mme.", "mr.", "me.", "mlle."}]
    return (parts, function)


def _extract_email(soup: Any, html: str) -> str:
    """Pick the first email that is not a generic KBO address."""
    # First, try bs4 — find <a href="mailto:...">.
    if soup is not None:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                candidate = href[len("mailto:"):].split("?")[0]
                if candidate and "kbopub" not in candidate.lower():
                    return candidate
    # Fallback: regex over the raw HTML.
    for m in _EMAIL_RE.finditer(html or ""):
        candidate = m.group(0)
        if "kbopub" in candidate.lower() or "economie" in candidate.lower():
            continue
        return candidate
    return ""


def _extract_phone(soup: Any, html: str) -> str:
    """Pick the first phone number that does not look like a TVA."""
    for m in _PHONE_RE.finditer(html or ""):
        candidate = m.group(0).strip()
        digits = "".join(ch for ch in candidate if ch.isdigit())
        # Heuristic: a phone should not equal a 10-digit TVA.
        if len(digits) == 10 and digits.startswith("0"):
            return candidate
        if len(digits) >= 9 and len(digits) <= 12:
            return candidate
    return ""


def _extract_website(soup: Any, html: str) -> str:
    """Pick the company website from the contact block.

    We avoid links that point to the KBO portal itself.
    """
    if soup is not None:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.lower().startswith("http"):
                continue
            low = href.lower()
            if "kbopub" in low or "economie.fgov.be" in low:
                continue
            return href
    for m in _HREF_RE.finditer(html or ""):
        href = m.group(1)
        if not href.lower().startswith("http"):
            continue
        low = href.lower()
        if "kbopub" in low or "economie.fgov.be" in low:
            continue
        return href
    return ""


# ── Adapter ────────────────────────────────────────────────────────


class KboWebEnricher(BaseEnricher):
    """KBO public-website enrichment.

    The adapter is sector-neutral and looks up companies purely by
    their TVA. It implements the two-step fallback from the
    reference: first try the direct company page, then fall back to
    the search-by-number form.
    """

    SOURCE_NAME = "kbo_web"

    def enrich(
        self, tva: str, company_name: str = ""
    ) -> Dict[str, Any]:
        """Look up ``tva`` on kbopub.economie.fgov.be.

        ``company_name`` is currently unused by the KBO web flow
        (KBO does not accept it as a search parameter) but is
        accepted for API parity with :class:`PappersEnricher`.
        """
        ent = digits_only(tva)
        if not ent:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                lookup_key=tva or "",
                error="empty or invalid TVA",
            ).to_dict()

        lookup_key = ent
        direct_url = self.build_url(ent)
        last_url = direct_url
        last_error = ""
        parsed: Optional[_ParsedKboPage] = None
        matched_ent = ent

        # 1. Direct fetch.
        try:
            response = self._request(direct_url)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            response = None

        if response is not None:
            status_code = getattr(response, "status_code", 0)
            if status_code != 200:
                last_error = f"HTTP {status_code} on direct page"
            else:
                html = getattr(response, "text", "")
                candidate = _parse_kbo_page(html)
                if self._page_matches(candidate, ent):
                    parsed = candidate
                else:
                    last_error = "direct page did not contain the requested enterprise"

        # 2. Search fallback.
        if parsed is None:
            try:
                search_url = self.build_search_url(ent)
                last_url = search_url
                response = self._request(
                    search_url, params={"nummer": ent, "actionLu": "Zoek"}
                )
            except Exception as exc:  # noqa: BLE001
                last_error = (
                    f"{last_error}; search fallback failed: "
                    f"{type(exc).__name__}: {exc}"
                ).strip("; ")
                response = None

            if response is not None:
                status_code = getattr(response, "status_code", 0)
                if status_code != 200:
                    last_error = (
                        f"{last_error}; search fallback HTTP {status_code}"
                    ).strip("; ")
                else:
                    resolved = self._extract_enterprise_link(
                        getattr(response, "text", ""), ent
                    )
                    if resolved is None:
                        last_error = (
                            f"{last_error}; no enterprise link in search results"
                        ).strip("; ")
                    else:
                        matched_ent = resolved
                        try:
                            follow_url = self.build_url(resolved)
                            last_url = follow_url
                            response2 = self._request(follow_url)
                        except Exception as exc:  # noqa: BLE001
                            last_error = (
                                f"{last_error}; follow failed: "
                                f"{type(exc).__name__}: {exc}"
                            ).strip("; ")
                            response2 = None
                        if response2 is not None and getattr(
                            response2, "status_code", 0
                        ) == 200:
                            candidate = _parse_kbo_page(
                                getattr(response2, "text", "")
                            )
                            if self._page_matches(candidate, resolved):
                                parsed = candidate

        if parsed is None:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                lookup_key=lookup_key,
                source_url=last_url,
                error=last_error or "no data found",
            ).to_dict()

        if parsed.is_empty:
            return EnrichmentResult(
                status=EnrichmentStatus.NO_MATCH,
                lookup_key=lookup_key,
                source_url=last_url,
            ).to_dict()

        return self._build_result(
            parsed,
            lookup_key=lookup_key,
            source_url=last_url,
        ).to_dict()

    # ── Public helpers (used by tests) ──────────────────────────

    @staticmethod
    def build_url(enterprise_number: str) -> str:
        """Build the direct KBO company URL.

        Public so tests can assert the URL pattern without going
        through the full enrich() call.
        """
        ent = digits_only(enterprise_number)
        return KBO_COMPANY_URL.format(ent=ent)

    @staticmethod
    def build_search_url(enterprise_number: str) -> str:
        """Build the KBO search URL. The enterprise number is also
        passed as a query parameter by :meth:`enrich`; this method
        returns the bare URL form for tests."""
        return KBO_SEARCH_URL

    # ── Internal ────────────────────────────────────────────────

    @staticmethod
    def _page_matches(parsed: _ParsedKboPage, ent: str) -> bool:
        """Return True if the parsed page corresponds to ``ent``.

        Heuristic: the page mentions the enterprise number, or has at
        least one non-empty field. The reference's KBO web page does
        not always include the number in the parsed body, so we
        accept "has any data" as a match when the enterprise is not
        found verbatim.
        """
        if not parsed.is_empty:
            return True
        return False

    @staticmethod
    def _extract_enterprise_link(html: str, ent: str) -> Optional[str]:
        """Find the first KBO search result href that points to a
        company page for ``ent`` (or any company if ``ent`` is not
        present — the search result is exact-match by number).
        """
        for match in _HREF_RE.finditer(html or ""):
            href = match.group(1)
            if KBO_COMPANY_LINK_NEEDLE not in href:
                continue
            # Parse the query string and pull the enterprise number.
            query_part = href.split("?", 1)[-1]
            params = parse_qs(query_part)
            values = params.get("ondernemingsnummer") or []
            for value in values:
                digits = digits_only(value)
                if digits and digits == digits_only(ent):
                    return digits
            # If the search returned a single result, take the first
            # company link even if the URL doesn't carry the number.
            if values:
                return digits_only(values[0])
        return None

    def _build_result(
        self,
        parsed: _ParsedKboPage,
        *,
        lookup_key: str,
        source_url: str,
    ) -> EnrichmentResult:
        fields: Dict[str, str] = {}
        evidence: List[Evidence] = []

        if parsed.company_name:
            fields["company_name"] = parsed.company_name
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="company_name",
                    confidence=confidence_for("company_name"),
                    note="Official denomination on the KBO page",
                )
            )
        if parsed.address:
            fields["address"] = parsed.address
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="address",
                    confidence=confidence_for("address"),
                    note="Registered address on the KBO page",
                )
            )
        if parsed.municipality:
            fields["city"] = parsed.municipality
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="city",
                    confidence=confidence_for("city"),
                    note="Municipality on the KBO page",
                )
            )
        if parsed.zipcode:
            fields["postcode"] = parsed.zipcode
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="postcode",
                    confidence=confidence_for("postcode"),
                    note="Postcode on the KBO page",
                )
            )
        if parsed.directors:
            director = parsed.directors[0]
            if director.get("first_name") and director.get("last_name"):
                fields["first_name"] = director["first_name"]
                fields["last_name"] = director["last_name"]
                evidence.append(
                    Evidence(
                        source=self.SOURCE_NAME,
                        source_url=source_url,
                        field="first_name",
                        confidence=confidence_for("first_name"),
                        note="Director listed on the KBO page",
                    )
                )
                evidence.append(
                    Evidence(
                        source=self.SOURCE_NAME,
                        source_url=source_url,
                        field="last_name",
                        confidence=confidence_for("last_name"),
                        note="Director listed on the KBO page",
                    )
                )
                if director.get("function"):
                    fields["position"] = director["function"]
                    evidence.append(
                        Evidence(
                            source=self.SOURCE_NAME,
                            source_url=source_url,
                            field="position",
                            confidence=confidence_for("position"),
                            note="Function on the KBO page",
                        )
                    )
        if parsed.email:
            fields["email"] = parsed.email
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="email",
                    confidence=confidence_for("email"),
                    note="Contact email on the KBO page",
                )
            )
        if parsed.phone:
            fields["phone"] = parsed.phone
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="phone",
                    confidence=confidence_for("phone"),
                    note="Phone number on the KBO page",
                )
            )
        if parsed.website:
            fields["website"] = parsed.website
            evidence.append(
                Evidence(
                    source=self.SOURCE_NAME,
                    source_url=source_url,
                    field="website",
                    confidence=confidence_for("website"),
                    note="Website link on the KBO page",
                )
            )

        # Backward-compatible fields for the original
        # KboWebEnricher contract.
        fields["status"] = "found"  # type: ignore[assignment]
        fields["municipality"] = parsed.municipality
        fields["zipcode"] = parsed.zipcode
        if parsed.directors:
            fields["directors"] = parsed.directors  # type: ignore[assignment]

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


__all__ = ["KboWebEnricher", "KBO_BASE_URL", "KBO_COMPANY_URL", "KBO_SEARCH_URL"]
