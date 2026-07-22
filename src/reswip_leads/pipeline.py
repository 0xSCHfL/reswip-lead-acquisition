"""Lead acquisition pipeline orchestration.

Connects source import → normalization → KBO verification → enrichment
→ deduplication → Zoho export → report generation.

The pipeline is sector-neutral: behavior is driven from a YAML profile
loaded via :mod:`reswip_leads.core.profile`. Insurance, energy, and
future sectors reuse the same pipeline.

Two entry points are provided:

- :class:`Pipeline` — the original file-system based run with a
  ``PipelineConfig`` and a JSON report written to ``<output>/Reports``.
- :class:`LeadPipeline` — a lightweight, dependency-injected runner
  that exposes a :class:`PipelineResult` with per-stage metrics. The
  CLI in this module uses :func:`run_pipeline` (built on top of
  ``LeadPipeline``).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from reswip_leads.core.fields import classify_language, classify_province, classify_region
from reswip_leads.core.models import Lead
from reswip_leads.core.profile import Profile, load_profile
from reswip_leads.deduplication.dedupe import DedupeResult, deduplicate
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


# ── Result types ───────────────────────────────────────────────────


@dataclass
class PipelineResult:
    """Result of a full pipeline run.

    Carries the final leads, a JSON-serialisable report, and the output
    paths that were written. Used by both the original :class:`Pipeline`
    and the lighter :class:`LeadPipeline`.
    """

    leads: List[Lead]
    report: Dict[str, Any]
    paths: Dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineStageMetrics:
    """Metrics for a single stage of the pipeline.

    ``notes`` carries stage-specific counters (e.g. ``verified``,
    ``inactive``, ``duplicates_removed``) so tests and operators can
    assert on per-stage behavior without inspecting the lead list.
    """

    name: str
    input_count: int
    output_count: int
    errors: List[str] = field(default_factory=list)
    notes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "input_count": self.input_count,
            "output_count": self.output_count,
            "errors": list(self.errors),
            "notes": dict(self.notes),
        }


# ── Pipeline ───────────────────────────────────────────────────────


class Pipeline:
    """Sector-neutral lead acquisition pipeline.

    Reads a single source CSV, normalizes fields, optionally verifies
    each company against the KBO ZIP, deduplicates, and writes
    Normalized / Enriched / Clean / CRM-ready / Reports output files.
    """

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


# ── Lightweight, dependency-injected pipeline ──────────────────────


@dataclass
class LeadPipelineResult:
    """Result of a :class:`LeadPipeline` run.

    Holds per-stage metrics and the final lead list so callers can
    inspect intermediate counts without re-running the pipeline.
    """

    profile: str
    output_path: str
    stages: List[PipelineStageMetrics]
    leads: List[Lead]
    duration_seconds: float
    success: bool
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "output_path": self.output_path,
            "stages": [s.to_dict() for s in self.stages],
            "lead_count": len(self.leads),
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "error": self.error,
        }


class LeadPipeline:
    """Lightweight, dependency-injected pipeline runner.

    Same logical stages as :class:`Pipeline` (import → classify →
    verify → enrich → dedupe → export) but with full dependency
    injection: every I/O and network boundary can be replaced. Use this
    in tests, in scripts that already have adapters wired, and from the
    CLI in this module.
    """

    def __init__(
        self,
        profile: Profile,
        output_path: str,
        input_csvs: Optional[Sequence[str]] = None,
        kbo_zip_path: Optional[str] = None,
        output_format: str = "csv",
        kbo_reader: Optional[Any] = None,
        kbo_verifier: Optional[Any] = None,
        pappers: Optional[Any] = None,
        kbo_web: Optional[Any] = None,
        importer: Optional[IQualifImporter] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> None:
        self.profile = profile
        self.output_path = str(output_path)
        self.input_csvs = list(input_csvs or [])
        self.kbo_zip_path = kbo_zip_path
        self.output_format = output_format.lower()
        if self.output_format not in {"csv", "xlsx"}:
            raise ValueError(
                f"Unsupported output_format: {output_format!r} (expected 'csv' or 'xlsx')"
            )

        # Dependency-injected collaborators. None = skip that stage.
        self.importer = importer or IQualifImporter()
        self.kbo_reader = kbo_reader
        self.kbo_verifier = kbo_verifier
        self.pappers = pappers
        self.kbo_web = kbo_web

        self.progress = progress
        self._stages: List[PipelineStageMetrics] = []

    # ── Public entry point ──────────────────────────────────────

    def run(self) -> LeadPipelineResult:
        """Run all stages in order. Returns a :class:`LeadPipelineResult`."""
        started = time.monotonic()
        try:
            leads = self._stage_import()
            if not leads:
                return self._finalize(
                    leads=[],
                    success=False,
                    error="import produced no leads",
                    started=started,
                )

            leads = self._stage_classify(leads)
            leads = self._stage_verify(leads)
            leads = self._stage_enrich(leads)
            leads = self._stage_dedupe(leads)
            self._stage_export(leads)
        except Exception as exc:  # noqa: BLE001
            return self._finalize(
                leads=[], success=False, error=str(exc), started=started
            )

        return self._finalize(leads=leads, success=True, started=started)

    # ── Stages ──────────────────────────────────────────────────

    def _stage_import(self) -> List[Lead]:
        metrics = PipelineStageMetrics(name="import", input_count=0, output_count=0)
        leads: List[Lead] = []
        try:
            if self.input_csvs:
                leads = self.importer.import_leads(list(self.input_csvs))
        except Exception as exc:  # noqa: BLE001
            metrics.errors.append(f"importer failed: {exc}")

        metrics.input_count = 0
        metrics.output_count = len(leads)
        metrics.notes["imported_count"] = len(leads)
        self._record(metrics)
        return leads

    def _stage_classify(self, leads: List[Lead]) -> List[Lead]:
        metrics = PipelineStageMetrics(
            name="classify", input_count=len(leads), output_count=len(leads)
        )
        classified = 0
        for lead in leads:
            try:
                if lead.province:
                    canonical = classify_province(lead.province)
                    if canonical:
                        lead.province = canonical
                    if not lead.region:
                        lead.region = classify_region(lead.province)
                    if not lead.language:
                        lead.language = classify_language(lead.province)
                classified += 1
            except Exception as exc:  # noqa: BLE001
                metrics.errors.append(
                    f"classify failed for {lead.tva or lead.company_name!r}: {exc}"
                )
        metrics.notes["classified_count"] = classified
        self._record(metrics)
        return leads

    def _stage_verify(self, leads: List[Lead]) -> List[Lead]:
        metrics = PipelineStageMetrics(
            name="verify", input_count=len(leads), output_count=len(leads)
        )

        if not self.kbo_zip_path or self.kbo_reader is None:
            metrics.notes["skipped"] = True
            self._record(metrics)
            return leads

        # Build the TVA index once for the whole batch.
        tv_as = {lead.tva for lead in leads if lead.tva}
        index: Dict[str, Any] = {}
        try:
            index = self.kbo_reader.build_index(
                str(self.kbo_zip_path), targets=tv_as
            )
        except Exception as exc:  # noqa: BLE001
            metrics.errors.append(f"KBO index build failed: {exc}")
            self._record(metrics)
            return leads

        verified = inactive = not_found = 0
        for lead in leads:
            if not lead.tva:
                continue
            try:
                record = index.get(lead.tva)
                if record is None and self.kbo_verifier is not None:
                    result = self.kbo_verifier.verify(lead.tva)
                    status = result.get("status", "not_found")
                else:
                    status = getattr(record, "status", "") or "verified"
                if status in {"AC", "verified"}:
                    verified += 1
                elif status in {"inactive", "INACTIVE"}:
                    inactive += 1
                else:
                    not_found += 1
                # Cross-fill: only fill empty fields, never overwrite.
                if record is not None:
                    self._fill_if_empty(lead, "company_name", getattr(record, "denomination", ""))
                    self._fill_if_empty(lead, "address", getattr(record, "address", ""))
                    self._fill_if_empty(lead, "city", getattr(record, "municipality", ""))
                    self._fill_if_empty(lead, "postcode", getattr(record, "zipcode", ""))
                    self._fill_if_empty(lead, "email", getattr(record, "email", ""))
                    self._fill_if_empty(lead, "phone", getattr(record, "phone", ""))
                    self._fill_if_empty(lead, "website", getattr(record, "website", ""))
                    activity_codes = getattr(record, "activity_codes", None)
                    if activity_codes:
                        lead.nace_codes = ",".join(sorted(activity_codes))
                    lead.status = status
            except Exception as exc:  # noqa: BLE001
                not_found += 1
                metrics.errors.append(f"verify failed for {lead.tva}: {exc}")

        metrics.notes.update(
            {
                "verified": verified,
                "inactive": inactive,
                "not_found": not_found,
                "index_size": len(index),
            }
        )
        self._record(metrics)
        return leads

    def _stage_enrich(self, leads: List[Lead]) -> List[Lead]:
        metrics = PipelineStageMetrics(
            name="enrich", input_count=len(leads), output_count=len(leads)
        )
        enriched = 0
        for lead in leads:
            if not lead.tva:
                continue
            for enricher in (self.pappers, self.kbo_web):
                if enricher is None:
                    continue
                try:
                    if self._apply_enrichment(lead, enricher):
                        enriched += 1
                except Exception as exc:  # noqa: BLE001
                    metrics.errors.append(
                        f"enrich failed for {lead.tva} via "
                        f"{type(enricher).__name__}: {exc}"
                    )
        metrics.notes["enriched_count"] = enriched
        self._record(metrics)
        return leads

    def _stage_dedupe(self, leads: List[Lead]) -> List[Lead]:
        metrics = PipelineStageMetrics(
            name="dedupe", input_count=len(leads), output_count=len(leads)
        )
        result: DedupeResult = deduplicate(leads)
        metrics.output_count = result.output_count
        metrics.notes["duplicates_removed"] = result.input_count - result.output_count
        metrics.notes["duplicate_tv_as"] = list(result.duplicates)
        self._record(metrics)
        return result.leads

    def _stage_export(self, leads: List[Lead]) -> None:
        metrics = PipelineStageMetrics(
            name="export", input_count=len(leads), output_count=len(leads)
        )
        try:
            if self.output_format == "xlsx":
                export_xlsx(leads, self.output_path, profile=self.profile)
            else:
                export_csv(leads, self.output_path, profile=self.profile)
            metrics.notes["format"] = self.output_format
        except Exception as exc:  # noqa: BLE001
            metrics.errors.append(f"export failed: {exc}")
        self._record(metrics)

    # ── Helpers ─────────────────────────────────────────────────

    def _apply_enrichment(self, lead: Lead, enricher: Any) -> bool:
        """Apply one enricher to one lead. Returns True if anything changed."""
        if not hasattr(enricher, "enrich"):
            return False
        try:
            try:
                result = enricher.enrich(lead.tva, lead.company_name)
            except TypeError:
                result = enricher.enrich(lead.tva)
        except Exception:
            raise
        if not isinstance(result, dict):
            return False

        changed = False
        # Direct attribute mappings.
        for src_key, dest_attr in (
            ("first_name", "first_name"),
            ("last_name", "last_name"),
            ("position", "position"),
            ("email", "email"),
            ("phone", "phone"),
            ("website", "website"),
        ):
            value = result.get(src_key)
            if not value:
                continue
            if self._fill_if_empty(lead, dest_attr, str(value)):
                changed = True

        # Pappers-style list fields.
        for src_key, dest_attr in (("emails", "email"), ("phones", "phone")):
            value = result.get(src_key)
            if isinstance(value, list) and value:
                if self._fill_if_empty(lead, dest_attr, str(value[0])):
                    changed = True

        # Director fallback — only if no decision-maker is present yet.
        directors = result.get("directors") or []
        if directors and not lead.first_name and not lead.last_name:
            first = directors[0]
            if isinstance(first, (list, tuple)) and len(first) >= 2:
                if self._fill_if_empty(lead, "first_name", str(first[0])):
                    changed = True
                if self._fill_if_empty(lead, "last_name", str(first[1])):
                    changed = True
            elif isinstance(first, dict):
                if self._fill_if_empty(lead, "first_name", str(first.get("first_name", ""))):
                    changed = True
                if self._fill_if_empty(lead, "last_name", str(first.get("last_name", ""))):
                    changed = True
                if self._fill_if_empty(lead, "position", str(first.get("function", ""))):
                    changed = True
        return changed

    @staticmethod
    def _fill_if_empty(lead: Lead, attr: str, value: str) -> bool:
        """Set ``lead.<attr>`` to ``value`` only if it is currently empty.

        Mirrors the merge policy in :mod:`dedupe._merge_into`: never
        overwrite a non-empty existing value with an empty or new value.
        Returns True if a value was set.
        """
        if not value:
            return False
        current = getattr(lead, attr, None)
        if current not in (None, ""):
            return False
        setattr(lead, attr, value.strip() if isinstance(value, str) else value)
        return True

    def _record(self, metrics: PipelineStageMetrics) -> None:
        self._stages.append(metrics)
        if self.progress is not None:
            self.progress(metrics.name, metrics.input_count, metrics.output_count)

    def _finalize(
        self,
        leads: List[Lead],
        success: bool,
        started: float,
        error: str = "",
    ) -> LeadPipelineResult:
        duration = time.monotonic() - started
        return LeadPipelineResult(
            profile=self.profile.name,
            output_path=self.output_path,
            stages=list(self._stages),
            leads=leads,
            duration_seconds=duration,
            success=success,
            error=error,
        )


# ── Convenience wrapper ────────────────────────────────────────────


def run_pipeline(
    profile_name: str,
    input_csvs: Sequence[str],
    output_path: str,
    kbo_zip_path: Optional[str] = None,
    output_format: str = "csv",
    **kwargs: Any,
) -> LeadPipelineResult:
    """Build a :class:`LeadPipeline` from a profile name and run it.

    All collaborators can still be overridden through ``**kwargs`` for
    tests; production callers can ignore them.
    """
    profile = load_profile(profile_name)
    pipeline = LeadPipeline(
        profile=profile,
        output_path=output_path,
        input_csvs=input_csvs,
        kbo_zip_path=kbo_zip_path,
        output_format=output_format,
        **kwargs,
    )
    return pipeline.run()


# ── CLI entry point ────────────────────────────────────────────────


def main() -> None:
    """Command-line interface for the pipeline."""
    parser = argparse.ArgumentParser(
        description=(
            "Lead acquisition pipeline: import → classify → KBO verify → "
            "enrich → dedupe → export."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", required=True, help="Path to sector profile YAML or sector name.")
    parser.add_argument(
        "--input", required=True, nargs="+", help="One or more input CSV file paths."
    )
    parser.add_argument("--output", required=True, help="Output CSV or XLSX path.")
    parser.add_argument("--kbo-zip", default=None, help="Path to KBO ZIP file.")
    parser.add_argument(
        "--format",
        choices=["csv", "xlsx"],
        default="csv",
        help="Output format (default: csv).",
    )

    args = parser.parse_args()

    profile_path = Path(args.profile)
    if profile_path.exists() and profile_path.suffix in {".yaml", ".yml"}:
        profile_name = profile_path.stem
    else:
        profile_name = args.profile

    result = run_pipeline(
        profile_name=profile_name,
        input_csvs=args.input,
        output_path=args.output,
        kbo_zip_path=args.kbo_zip,
        output_format=args.format,
    )

    print(
        f"Pipeline {'succeeded' if result.success else 'failed'} "
        f"in {result.duration_seconds:.2f}s"
    )
    if result.error:
        print(f"  Error: {result.error}")
    for stage in result.stages:
        print(f"  {stage.name}: {stage.input_count} → {stage.output_count}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
