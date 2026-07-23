from __future__ import annotations

import abc
import re
from dataclasses import dataclass
from typing import Optional


_REJECTED_PREFIXES = ("noreply", "no-reply", "donotreply")

_REJECTED_DOMAINS = frozenset(
    {
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
    }
)


@dataclass
class EmailCandidate:
    email: str
    source: str
    source_url: str
    confidence: str  # "High" | "Medium" | "Low"
    note: str = ""


def _is_valid_email(email: str, website_domain: str = "") -> bool:
    if not email or "@" not in email:
        return False

    parts = email.split("@")
    if len(parts) != 2:
        return False

    local, domain = parts
    if not local or not domain or "." not in domain:
        return False

    domain_lower = domain.lower()
    local_lower = local.lower()

    if domain_lower in _REJECTED_DOMAINS:
        return False

    for prefix in _REJECTED_PREFIXES:
        if local_lower.startswith(prefix):
            return False

    if local_lower == "info" and website_domain:
        if domain_lower == website_domain.lower():
            return True
        return False

    if local_lower == "info":
        return False

    return True


class BaseEmailSource(abc.ABC):
    @abc.abstractmethod
    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        ...
