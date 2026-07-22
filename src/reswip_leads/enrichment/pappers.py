"""Pappers.be company enrichment.

Scrapes pappers.be for director names, emails, and contact data.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import unquote


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    return s.strip("-")


class PappersEnricher:
    """Enrich company data from pappers.be.

    Sector-neutral: finds directors and public contact info
    without any insurance-specific filtering.
    """

    def enrich(self, tva: str, company_name: str) -> Dict[str, Any]:
        """Scrape pappers.be for a company.

        Returns a dict with:
        - ``directors``: list of (first_name, last_name) tuples
        - ``emails``: list of email addresses found
        - ``phones``: list of phone numbers found
        - ``website``: company website URL
        """
        # Stub — real implementation will make HTTP requests
        enterprise = tva.replace("BE", "") if tva.startswith("BE") else tva
        return {
            "directors": [],
            "emails": [],
            "phones": [],
            "website": "",
            "enterprise_number": enterprise,
        }

    def fetch_directors(self, company_name: str, enterprise_number: str) -> list[tuple[str, str]]:
        """Fetch director names from pappers.be.

        Returns list of (first_name, last_name) tuples.
        """
        # Stub
        return []
