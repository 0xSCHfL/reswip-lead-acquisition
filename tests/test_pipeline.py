"""Tests for pipeline orchestration."""
import csv
import json
import os
import pytest
from pathlib import Path

from reswip_leads.pipeline import Pipeline, PipelineConfig


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def energy_profile_path():
    return str(Path(__file__).resolve().parent.parent / "profiles" / "energy.yaml")


@pytest.fixture
def sample_csv(tmp_path):
    """Create a minimal iQualif-style CSV fixture."""
    csv_path = tmp_path / "input.csv"
    rows = [
        {
            "Company Name": "SolarTech Belgium",
            "VAT Number": "0123456789",
            "Address": "Rue du Soleil 10",
            "City": "Namur",
            "Province": "Namur",
            "Email": "info@solartech.be",
            "Phone": "+3281234567",
        },
        {
            "Company Name": "WindPower SCRL",
            "VAT Number": "0415678901",
            "Address": "Steenveldstraat 5",
            "City": "Antwerpen",
            "Province": "Antwerpen",
            "Email": "contact@windpower.be",
        },
        {
            "Company Name": "SolarTech Belgium",
            "VAT Number": "0123456789",
            "Address": "Rue du Soleil 10",
            "City": "Namur",
            "Phone": "+32471234567",
        },
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return str(csv_path)


@pytest.fixture
def output_dir(tmp_path):
    return str(tmp_path / "output")


# ── PipelineConfig ─────────────────────────────────────────────────


class TestPipelineConfig:
    def test_config_defaults(self):
        config = PipelineConfig(
            profile_path="profiles/energy.yaml",
            input_path="data/input.csv",
            output_dir="output/",
        )
        assert config.skip_kbo is False
        assert config.skip_enrichment is False
        assert config.skip_dedupe is False
        assert config.export_format == "csv"
        assert config.dry_run is False
        assert config.force is False
        assert config.kbo_zip is None

    def test_config_custom(self):
        config = PipelineConfig(
            profile_path="profiles/energy.yaml",
            input_path="data/input.csv",
            output_dir="output/",
            skip_kbo=True,
            skip_enrichment=True,
            skip_dedupe=True,
            export_format="xlsx",
            dry_run=True,
            force=True,
            kbo_zip="data/kbo.zip",
        )
        assert config.skip_kbo is True
        assert config.skip_enrichment is True
        assert config.skip_dedupe is True
        assert config.export_format == "xlsx"
        assert config.dry_run is True
        assert config.force is True
        assert config.kbo_zip == "data/kbo.zip"


# ── Profile Selection ──────────────────────────────────────────────


class TestProfileSelection:
    def test_energy_profile(self, energy_profile_path):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path="dummy.csv",
            output_dir="dummy/",
        )
        pipeline = Pipeline(config)
        assert pipeline.profile.name == "energy"

    def test_insurance_profile(self, tmp_path):
        prof_path = tmp_path / "insurance.yaml"
        prof_path.write_text(
            "name: insurance\n"
            "description: Insurance\n"
            "sources: [iqualif]\n"
            "filters:\n  industry: [insurance]\n"
            "enrichment: [company_info]\n"
            "organization: Reswip Insurance\n"
            "lead_source: Insurance Prospect\n"
        )
        config = PipelineConfig(
            profile_path=str(prof_path),
            input_path="dummy.csv",
            output_dir="dummy/",
        )
        pipeline = Pipeline(config)
        assert pipeline.profile.name == "insurance"


# ── Output Directory Creation ──────────────────────────────────────


class TestOutputDirectoryCreation:
    def test_creates_subdirectories(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        pipeline.run()

        assert os.path.isdir(os.path.join(output_dir, "Normalized"))
        assert os.path.isdir(os.path.join(output_dir, "Enriched"))
        assert os.path.isdir(os.path.join(output_dir, "Clean"))
        assert os.path.isdir(os.path.join(output_dir, "CRM Ready"))
        assert os.path.isdir(os.path.join(output_dir, "Reports"))


# ── Normalized Output ──────────────────────────────────────────────


class TestNormalizedOutput:
    def test_normalized_csv_created(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert os.path.exists(result.paths["normalized"])

    def test_normalized_tva_format(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        with open(result.paths["normalized"], encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        tvas = {r["VAT Number"] for r in rows}
        assert "BE0123456789" in tvas
        assert "BE0415678901" in tvas


# ── Deduplication Integration ──────────────────────────────────────


class TestDeduplication:
    def test_dedupe_reduces_rows(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        # Input has 3 rows, 2 unique TVAs → output should have 2
        assert result.report["final_row_count"] == 2

    def test_skip_dedupe_preserves_all(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
            skip_dedupe=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        # Without dedup, all 3 rows preserved
        assert result.report["final_row_count"] == 3

    def test_dedupe_report_counts(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert result.report["duplicate_count"] >= 1


# ── Zoho Export Integration ────────────────────────────────────────


class TestZohoExport:
    def test_crm_csv_created(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert os.path.exists(result.paths["crm"])

    def test_crm_csv_columns(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        with open(result.paths["crm"], encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
        assert "Business Name" in header
        assert "TVA Number" in header
        assert "Organization" in header
        assert "Lead Source" in header

    def test_profile_defaults_in_crm(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        with open(result.paths["crm"], encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # All rows should have Organization from profile
        for row in rows:
            assert row["Organization"] != ""


# ── Report Generation ──────────────────────────────────────────────


class TestReportGeneration:
    def test_report_json_created(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert os.path.exists(result.paths["report"])

    def test_report_contains_required_fields(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        with open(result.paths["report"], encoding="utf-8") as f:
            report = json.load(f)

        assert "input_row_count" in report
        assert "normalized_row_count" in report
        assert "kbo_matched_count" in report
        assert "enrichment_count" in report
        assert "duplicate_count" in report
        assert "final_row_count" in report
        assert "output_paths" in report
        assert "warnings" in report
        assert "errors" in report

    def test_report_row_counts(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        report = result.report
        assert report["input_row_count"] == 3
        assert report["normalized_row_count"] == 3
        assert report["final_row_count"] == 2
        assert report["kbo_matched_count"] == 0
        assert report["enrichment_count"] == 0


# ── Dry Run Mode ───────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_creates_no_files(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
            dry_run=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        # Dry run should still produce a result but not write files
        assert result.report["input_row_count"] == 3
        # Output dir should not have the subdirectories
        assert not os.path.isdir(os.path.join(output_dir, "Normalized"))

    def test_dry_run_report_has_counts(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
            dry_run=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert result.report["final_row_count"] == 2


# ── Skip KBO ───────────────────────────────────────────────────────


class TestSkipKbo:
    def test_skip_kbo_no_verification(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert result.report["kbo_matched_count"] == 0


# ── Skip Enrichment ────────────────────────────────────────────────


class TestSkipEnrichment:
    def test_skip_enrichment_no_extra_data(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        assert result.report["enrichment_count"] == 0


# ── Overwrite Protection ───────────────────────────────────────────


class TestOverwriteProtection:
    def test_no_overwrite_by_default(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        pipeline.run()

        # Run again without force — should raise
        with pytest.raises(FileExistsError):
            pipeline.run()

    def test_force_overwrites(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        pipeline.run()

        config.force = True
        pipeline2 = Pipeline(config)
        result = pipeline2.run()
        assert result.report["final_row_count"] == 2


# ── Full Pipeline End-to-End ───────────────────────────────────────


class TestFullPipelineEndToEnd:
    def test_complete_flow(self, energy_profile_path, sample_csv, output_dir):
        config = PipelineConfig(
            profile_path=energy_profile_path,
            input_path=sample_csv,
            output_dir=output_dir,
            skip_kbo=True,
            skip_enrichment=True,
        )
        pipeline = Pipeline(config)
        result = pipeline.run()

        # All outputs exist
        assert os.path.exists(result.paths["normalized"])
        assert os.path.exists(result.paths["crm"])
        assert os.path.exists(result.paths["report"])

        # Report is valid
        report = result.report
        assert report["input_row_count"] == 3
        assert report["final_row_count"] == 2
        assert report["duplicate_count"] >= 1
        assert len(report["warnings"]) == 0
        assert len(report["errors"]) == 0
