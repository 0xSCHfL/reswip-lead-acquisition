"""KBO web page enrichment.

Scrapes kbopub.economie.fgov.be for company details and directors.
"""
from __future__ import annotations

from typing import Any, Dict


class KboWebEnricher:
    """Enrich company data from the KBO public web pages.

    Sector-neutral: fills missing fields from the official KBO
    company detail pages without insurance-specific logic.
    """

    def enrich(self, tva: str) -> Dict[str, Any]:
        """Scrape the KBO company page for a TVA number.

        Returns a dict with:
        - ``status``: ``found`` or ``not_found``
        - ``company_name``: official denomination
        - ``address``: registered address
        - ``municipality``: city
        - ``zipcode``: postal code
        - ``directors``: list of director dicts with first_name, last_name, function
        - ``email``: public email
        - ``phone``: public phone
        - ``website``: company website
        """
        # Stub — real implementation will scrape kbopub.economie.fgov.be
        enterprise = tva.replace("BE", "") if tva.startswith("BE") else tva
        return {
            "status": "not_found",
            "enterprise_number": enterprise,
            "company_name": "",
            "address": "",
            "municipality": "",
            "zipcode": "",
            "directors": [],
            "email": "",
            "phone": "",
            "website": "",
        }
