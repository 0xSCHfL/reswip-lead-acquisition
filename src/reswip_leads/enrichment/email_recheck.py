"""Email recheck enricher — orchestrates multiple email sources.

Chains email discovery sources in priority order (KBO ZIP → KBO web →
Pappers → website) and returns the first reliable email found.

Standalone CLI::

    python3 -m reswip_leads.enrichment.email_recheck \\
        --input leads.csv --output enriched.csv --source all
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
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
        self, tva: str, company_name: str = "", website_url: str = ""
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
        # Use provided website_url or fall back to stored context
        effective_website = website_url or self._website_url
        for source in self._sources:
            try:
                candidate = source.find_email(
                    tva=tva,
                    company_name=company_name,
                    website_url=effective_website,
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


# ── Standalone CLI ────────────────────────────────────────────────


def _load_proxy_file(path: str) -> Optional[Dict[str, str]]:
    """Load a proxy rotator file and return the first proxy as a requests-style dict."""
    try:
        from pathlib import Path
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return {"http": line, "https": line}
    except Exception:
        pass
    return None


def _build_sources(
    source_filter: str,
    config: EnrichmentConfig,
) -> List[BaseEmailSource]:
    """Build email sources based on the --source filter."""
    from reswip_leads.enrichment.email_sources import (
        KboEmailSource,
        PappersEmailSource,
        WebsiteEmailSource,
    )

    choice = (source_filter or "all").lower().strip()
    sources: List[BaseEmailSource] = []

    if choice in ("kbo", "all"):
        sources.append(KboEmailSource(config=config))
    if choice in ("pappers", "all"):
        sources.append(PappersEmailSource(config=config))
    if choice in ("website", "all"):
        sources.append(WebsiteEmailSource())

    return sources


def _process_csv(
    input_path: str,
    output_path: str,
    sources: List[BaseEmailSource],
    missing_only: bool = True,
) -> Dict[str, Any]:
    """Process a CSV file and enrich leads missing emails.

    Returns a summary dict with counts.
    """
    enricher = EmailRecheckEnricher(sources=sources)

    rows: List[Dict[str, str]] = []
    with open(input_path, "r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            rows.append(dict(row))

    if not rows:
        return {"total": 0, "processed": 0, "found": 0, "not_found": 0, "errors": 0}

    # Ensure required columns exist
    if "TVA" not in fieldnames:
        fieldnames.insert(0, "TVA")
    if "Email" not in fieldnames:
        fieldnames.append("Email")
    if "Email Source" not in fieldnames:
        fieldnames.append("Email Source")
    if "Email Confidence" not in fieldnames:
        fieldnames.append("Email Confidence")

    total = len(rows)
    processed = found = not_found = errors = 0

    for i, row in enumerate(rows):
        tva = row.get("TVA", "").strip()
        company_name = row.get("Company Name", "").strip()
        existing_email = row.get("Email", "").strip()
        website_url = row.get("Website", "").strip()

        if missing_only and existing_email:
            continue

        if not tva:
            errors += 1
            continue

        processed += 1
        try:
            result = enricher.enrich(tva, company_name, website_url=website_url)
            if result.get("status") == "enriched":
                email = result.get("email", "")
                if email:
                    row["Email"] = email
                    evidence = result.get("evidence", [])
                    if evidence:
                        row["Email Source"] = evidence[0].get("source", "")
                        row["Email Confidence"] = evidence[0].get("confidence", "")
                    found += 1
            else:
                not_found += 1
        except Exception as exc:
            errors += 1
            logger.debug("email_recheck: failed for %s: %s", tva, exc)

        # Progress indicator
        if (i + 1) % 10 == 0 or (i + 1) == total:
            print(f"\r  Processed {i + 1}/{total}...", end="", flush=True)

    print()  # newline after progress

    # Write output
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "total": total,
        "processed": processed,
        "found": found,
        "not_found": not_found,
        "errors": errors,
    }


def main() -> None:
    """CLI entry point for email recheck."""
    parser = argparse.ArgumentParser(
        description="Email recheck: discover missing emails from multiple sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", required=True, help="Input CSV file path."
    )
    parser.add_argument(
        "--output", required=True, help="Output CSV file path."
    )
    parser.add_argument(
        "--source",
        choices=["kbo", "pappers", "website", "all"],
        default="all",
        help="Email source(s) to use (default: all).",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        default=True,
        help="Only process leads missing email (default: true).",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="Process all leads, including those with existing email.",
    )
    parser.add_argument(
        "--proxy-file",
        default=None,
        help="Path to proxy rotator file (one proxy URL per line).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds (default: 15).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Number of HTTP retries (default: 2).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests in seconds (default: 0.5).",
    )

    args = parser.parse_args()

    proxy = _load_proxy_file(args.proxy_file) if args.proxy_file else None
    config = EnrichmentConfig(
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        proxy=proxy,
    )
    sources = _build_sources(args.source, config)
    missing_only = not args.include_existing

    print(f"Email Recheck — source: {args.source}")
    print(f"  Input: {args.input}")
    print(f"  Output: {args.output}")
    print()

    start = time.monotonic()
    stats = _process_csv(args.input, args.output, sources, missing_only=missing_only)
    duration = time.monotonic() - start

    print()
    print("Summary:")
    print(f"  Total rows: {stats['total']}")
    print(f"  Processed: {stats['processed']}")
    print(f"  Emails found: {stats['found']}")
    print(f"  Not found: {stats['not_found']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()
