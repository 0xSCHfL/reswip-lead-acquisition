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

from reswip_leads.enrichment.pappers import (
    PAPPERS_BASE_URL,
    _parse_pappers_page,
    slugify,
)
from reswip_leads.verification.kbo.zip_reader import KboZipReader


logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _is_valid_email(value: str) -> bool:
    """Return True if *value* looks like a plausible email address."""
    if not value or len(value) > 254:
        return False
    return _EMAIL_RE.fullmatch(value.strip()) is not None


@dataclass
class EmailCandidate:
    """An email discovered by an email source."""

    email: str
    source: str
    confidence: str  # "High" | "Medium" | "Low"


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


__all__ = [
    "BaseEmailSource",
    "EmailCandidate",
    "KboZipEmailSource",
    "PappersEmailSource",
    "_is_valid_email",
]
