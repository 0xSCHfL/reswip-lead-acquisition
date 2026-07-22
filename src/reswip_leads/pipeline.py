"""Lead acquisition pipeline orchestration.

Connects source import → normalization → KBO verification → enrichment
→ deduplication → Zoho export → report generation.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from reswip_leads.core.fields import classify_language, classify_province, classify_region
from reswip_leads.core.models import Lead
from reswip_leads.core.profile import Profile, load_profile
from reswip_leads.deduplication.dedupe import deduplicate
from reswip_leads.exports.zoho import export_csv, export_xlsx
from reswip_leads.sources.iqualif.importer import IQualifImporter


# ── Configuration ──────────────────────────────────────────────────


@dataclass
class PipelineConfig:
    """Configuration for a pipeline run."""

    profile_path: str
    input_path: str
    output_dir: str
    kbo_zip: Optional[str] = None
    skip_kbo: bool = False
    skip_enrichment: bool = False
    skip_dedupe: bool = False
    export_format: str = "csv"
    dry_run: bool = False
    force: bool = False


# ── Result ─────────────────────────────────────────────────────────


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    leads: List[Lead]
    report: Dict[str, Any]
    paths: Dict[str, str] = field(default_factory=dict)


# ── Pipeline ───────────────────────────────────────────────────────


class Pipeline:
    """Sector-neutral lead acquisition pipeline."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.profile = load_profile(config.profile_path)
        self._importer = IQualifImporter()
        self._warnings: List[str] = []
        self._errors: List[str] = []

    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
        name = Path(self.config.input_path).stem

        # 1. Load profile
        profile = self.profile

        # 2. Import source CSV
        raw_leads = self._importer.import_leads([self.config.input_path])
        input_count = len(raw_leads)

        # 3. Normalize
        normalized = self._normalize(raw_leads)
        normalized_count = len(normalized)

        # 4. KBO verification
        kbo_matched = 0
        if not self.config.skip_kbo and self.config.kbo_zip:
            kbo_matched = self._verify_kbo(normalized)

        # 5. Enrichment
        enrichment_count = 0
        if not self.config.skip_enrichment:
            enrichment_count = self._enrich(normalized)

        # 6. Deduplication
        duplicate_count = 0
        if not self.config.skip_dedupe:
            dedupe_result = deduplicate(normalized)
            normalized = dedupe_result.leads
            duplicate_count = len(dedupe_result.duplicates)

        final_count = len(normalized)

        # 7. Build paths
        paths = self._build_paths(name)

        # 8. Write outputs (unless dry run)
        if not self.config.dry_run:
            self._check_overwrite(paths)
            self._write_outputs(normalized, profile, paths)

        # 9. Build report
        report = {
            "input_row_count": input_count,
            "normalized_row_count": normalized_count,
            "kbo_matched_count": kbo_matched,
            "enrichment_count": enrichment_count,
            "duplicate_count": duplicate_count,
            "final_row_count": final_count,
            "output_paths": paths if not self.config.dry_run else {},
            "warnings": list(self._warnings),
            "errors": list(self._errors),
        }

        if not self.config.dry_run:
            report_path = paths["report"]
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, ensure_ascii=False)

        return PipelineResult(
            leads=normalized,
            report=report,
            paths=paths if not self.config.dry_run else {},
        )

    # ── Pipeline steps ──────────────────────────────────────────

    def _normalize(self, leads: List[Lead]) -> List[Lead]:
        """Normalize TVA, classify provinces, and clean fields."""
        for lead in leads:
            # TVA is already normalized by Lead.__post_init__
            # Classify province → region → language
            if lead.province:
                lead.province = classify_province(lead.province)
                if not lead.region:
                    lead.region = classify_region(lead.province)
                if not lead.language:
                    lead.language = classify_language(lead.province)
            # Clean phone numbers
            lead.phone = self._clean_phone(lead.phone)
            lead.mobile = self._clean_phone(lead.mobile)
            # Clean email
            lead.email = self._clean_email(lead.email)
        return leads

    def _verify_kbo(self, leads: List[Lead]) -> int:
        """Verify companies through KBO ZIP. Returns count of matches."""
        from reswip_leads.verification.kbo.verifier import KboVerifier

        verifier = KboVerifier()
        matched = 0
        for lead in leads:
            if not lead.tva:
                continue
            result = verifier.verify(lead.tva)
            if result.get("status") == "verified":
                matched += 1
                # Fill missing fields from KBO
                if not lead.address and result.get("address"):
                    lead.address = result["address"]
                if not lead.city and result.get("municipality"):
                    lead.city = result["municipality"]
                if not lead.province and result.get("zipcode"):
                    lead.province = result.get("province", "")
        return matched

    def _enrich(self, leads: List[Lead]) -> int:
        """Run enrichment adapters. Returns count of enriched leads."""
        # Enrichment is optional and adapter-based
        # For now, return 0 — adapters are wired when available
        return 0

    # ── Output helpers ──────────────────────────────────────────

    def _build_paths(self, name: str) -> Dict[str, str]:
        """Build output file paths."""
        base = self.config.output_dir
        ext = ".xlsx" if self.config.export_format == "xlsx" else ".csv"
        return {
            "normalized": os.path.join(base, "Normalized", f"{name}_normalized.csv"),
            "enriched": os.path.join(base, "Enriched", f"{name}_enriched.csv"),
            "clean": os.path.join(base, "Clean", f"{name}_clean.csv"),
            "crm": os.path.join(base, "CRM Ready", f"{name}_crm_ready{ext}"),
            "report": os.path.join(base, "Reports", f"{name}_report.json"),
        }

    def _check_overwrite(self, paths: Dict[str, str]) -> None:
        """Raise FileExistsError if any output exists and force is False."""
        if self.config.force:
            return
        existing = [p for p in paths.values() if os.path.exists(p)]
        if existing:
            raise FileExistsError(
                f"Output files already exist (use --force to overwrite): {existing[0]}"
            )

    def _write_outputs(
        self,
        leads: List[Lead],
        profile: Profile,
        paths: Dict[str, str],
    ) -> None:
        """Write all output files."""
        # Ensure directories exist
        for path in paths.values():
            os.makedirs(os.path.dirname(path), exist_ok=True)

        # Normalized CSV
        self._write_csv(leads, paths["normalized"])

        # Enriched CSV (same as normalized for now)
        self._write_csv(leads, paths["enriched"])

        # Clean CSV (same as normalized for now)
        self._write_csv(leads, paths["clean"])

        # CRM-ready export
        if self.config.export_format == "xlsx":
            export_xlsx(leads, paths["crm"], profile)
        else:
            export_csv(leads, paths["crm"], profile)

    def _write_csv(self, leads: List[Lead], path: str) -> None:
        """Write leads to a CSV file."""
        if not leads:
            return
        fieldnames = list(leads[0].to_dict().keys())
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for lead in leads:
                writer.writerow(lead.to_dict())

    # ── Field cleaning ──────────────────────────────────────────

    @staticmethod
    def _clean_phone(value: Optional[str]) -> str:
        """Clean a phone number: keep digits and + prefix."""
        if not value:
            return ""
        cleaned = re.sub(r"[^\d+]", "", value).strip()
        if cleaned and not cleaned.startswith("+"):
            # Convert 0xx to +32x for Belgian numbers
            if cleaned.startswith("0") and len(cleaned) >= 9:
                cleaned = "+32" + cleaned[1:]
        return cleaned

    @staticmethod
    def _clean_email(value: Optional[str]) -> str:
        """Validate and clean an email address."""
        if not value:
            return ""
        value = value.strip().lower()
        if re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", value):
            return value
        return ""


# ── CLI entry point ────────────────────────────────────────────────


def main() -> None:
    """Command-line interface for the pipeline."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Lead acquisition pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", required=True, help="Path to sector profile YAML")
    parser.add_argument("--input", required=True, help="Input CSV file path")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--kbo-zip", default=None, help="Path to KBO ZIP file")
    parser.add_argument("--skip-kbo", action="store_true", help="Skip KBO verification")
    parser.add_argument("--skip-enrichment", action="store_true", help="Skip enrichment")
    parser.add_argument("--skip-dedupe", action="store_true", help="Skip deduplication")
    parser.add_argument(
        "--export-format",
        choices=["csv", "xlsx"],
        default="csv",
        help="Export format (default: csv)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no file writes)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs")

    args = parser.parse_args()

    config = PipelineConfig(
        profile_path=args.profile,
        input_path=args.input,
        output_dir=args.output_dir,
        kbo_zip=args.kbo_zip,
        skip_kbo=args.skip_kbo,
        skip_enrichment=args.skip_enrichment,
        skip_dedupe=args.skip_dedupe,
        export_format=args.export_format,
        dry_run=args.dry_run,
        force=args.force,
    )

    pipeline = Pipeline(config)
    result = pipeline.run()

    print(f"Pipeline complete.")
    print(f"  Input rows:      {result.report['input_row_count']}")
    print(f"  Normalized:      {result.report['normalized_row_count']}")
    print(f"  KBO matched:     {result.report['kbo_matched_count']}")
    print(f"  Enriched:        {result.report['enrichment_count']}")
    print(f"  Duplicates:      {result.report['duplicate_count']}")
    print(f"  Final rows:      {result.report['final_row_count']}")

    if result.report["warnings"]:
        print(f"\nWarnings ({len(result.report['warnings'])}):")
        for w in result.report["warnings"]:
            print(f"  - {w}")

    if result.report["errors"]:
        print(f"\nErrors ({len(result.report['errors'])}):")
        for e in result.report["errors"]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
