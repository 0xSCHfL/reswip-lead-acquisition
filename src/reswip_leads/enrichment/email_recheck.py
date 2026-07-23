"""Email recheck enricher — orchestrates multiple email sources.

Chains email discovery sources in priority order (KBO ZIP → KBO web →
Pappers → website) and returns the first reliable email found.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from reswip_leads.enrichment.base import (
    EnrichmentConfig,
    EnrichmentResult,
    EnrichmentStatus,
    Evidence,
    confidence_for,
    digits_only,
)
from reswip_leads.enrichment.email_sources import (
    BaseEmailSource,
    EmailCandidate,
)


logger = logging.getLogger(__name__)


class EmailRecheckEnricher:
    """Orchestrate email discovery from multiple sources.

    Sources are tried in the order provided. The first source that
    returns an :class:`EmailCandidate` wins.
    """

    def __init__(self, sources: Optional[List[BaseEmailSource]] = None) -> None:
        self._sources: List[BaseEmailSource] = list(sources or [])
        self._website_url: str = ""

    def set_lead_context(self, lead: Any) -> None:
        """Store lead context (e.g. website_url) for sources that need it."""
        self._website_url = getattr(lead, "website", "") or ""

    def enrich(
        self, tva: str, company_name: str = ""
    ) -> Dict[str, Any]:
        """Try each source in order. Returns a flat dict compatible
        with the pipeline's enrichment contract.
        """
        if not tva:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                lookup_key="",
                error="empty or invalid TVA",
            ).to_dict()

        lookup_key = digits_only(tva)
        for source in self._sources:
            try:
                candidate = source.find_email(
                    tva=tva,
                    company_name=company_name,
                    website_url=self._website_url,
                )
            except Exception as exc:
                logger.debug(
                    "EmailRecheckEnricher: %s failed for %s: %s",
                    type(source).__name__,
                    tva,
                    exc,
                )
                continue

            if candidate is None:
                continue

            return self._build_result(candidate, lookup_key).to_dict()

        return EnrichmentResult(
            status=EnrichmentStatus.NO_MATCH,
            lookup_key=lookup_key,
        ).to_dict()

    def _build_result(
        self, candidate: EmailCandidate, lookup_key: str
    ) -> EnrichmentResult:
        """Convert an EmailCandidate into an EnrichmentResult."""
        fields = {"email": candidate.email}
        evidence = [
            Evidence(
                source=candidate.source,
                source_url=candidate.source_url,
                field="email",
                confidence=candidate.confidence,
                note=f"Email discovered via {candidate.source}",
            )
        ]
        return EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields=fields,
            evidence=evidence,
            lookup_key=lookup_key,
            source_url=candidate.source_url,
        )


__all__ = ["EmailRecheckEnricher"]
