"""Tests for pipeline orchestration."""
import csv
import json
import os
import subprocess
import sys
import pytest
from pathlib import Path
from typing import Any, Dict, List, Optional

from reswip_leads.core.models import Lead
from reswip_leads.core.profile import Profile
from reswip_leads.pipeline import (
    LeadPipeline,
    LeadPipelineResult,
    Pipeline,
    PipelineConfig,
    PipelineStageMetrics,
    run_pipeline,
)


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


# ── Fakes for the lightweight LeadPipeline runner ──────────────────


class _FakeImporter:
    """Drop-in replacement for :class:`IQualifImporter` returning a fixed list."""

    def __init__(self, leads: List[Lead]) -> None:
        self._leads = leads
        self.calls: List[List[str]] = []

    def import_leads(self, csv_paths: List[str]) -> List[Lead]:
        self.calls.append(list(csv_paths))
        return list(self._leads)


class _FailingImporter(_FakeImporter):
    def import_leads(self, csv_paths: List[str]) -> List[Lead]:  # type: ignore[override]
        raise RuntimeError("simulated import failure")


class _FakeKboRecord:
    def __init__(
        self,
        status: str = "AC",
        denomination: str = "",
        address: str = "",
        municipality: str = "",
        zipcode: str = "",
        email: str = "",
        phone: str = "",
        website: str = "",
        activity_codes: Optional[set] = None,
    ) -> None:
        self.status = status
        self.denomination = denomination
        self.address = address
        self.municipality = municipality
        self.zipcode = zipcode
        self.email = email
        self.phone = phone
        self.website = website
        self.activity_codes = activity_codes or set()


class _FakeKboReader:
    def __init__(self, index: Dict[str, _FakeKboRecord]) -> None:
        self._index = dict(index)
        self.calls: List[Dict[str, Any]] = []

    def build_index(self, zip_path: str, targets: set, **kwargs: Any) -> Dict[str, _FakeKboRecord]:
        self.calls.append({"zip_path": zip_path, "targets": set(targets)})
        return {k: v for k, v in self._index.items() if k in targets}


class _FakeKboVerifier:
    def __init__(self, statuses: Optional[Dict[str, str]] = None) -> None:
        self.statuses = statuses or {}
        self.calls: List[str] = []

    def verify(self, tva: str) -> Dict[str, Any]:
        self.calls.append(tva)
        return {"status": self.statuses.get(tva, "not_found"), "enterprise_number": tva}


class _FakeEnricher:
    def __init__(
        self,
        result: Optional[Dict[str, Any]] = None,
        signature: str = "two",
        raises: bool = False,
    ) -> None:
        self.result = result
        self.signature = signature
        self.raises = raises
        self.calls: List[Any] = []

    def enrich(self, *args: Any) -> Dict[str, Any]:
        self.calls.append(args)
        if self.raises:
            raise RuntimeError("simulated network failure")
        return self.result or {}


# ── LeadPipeline fixtures ──────────────────────────────────────────


@pytest.fixture
def energy_profile() -> Profile:
    return Profile(
        name="energy",
        description="",
        sources=["iqualif", "kbo"],
        filters={"industry": ["energy"]},
        enrichment=["company_info"],
    )


@pytest.fixture
def three_leads() -> List[Lead]:
    return [
        Lead(company_name="Acme", tva="0123456789", province="Hainaut"),
        Lead(company_name="Beta Corp", tva="0415678901", province="Liège"),
        Lead(company_name="Gamma SA", tva="0412345678", province="Bruxelles"),
    ]


# ── LeadPipeline: happy path ──────────────────────────────────────


class TestLeadPipelineHappyPath:
    def test_runs_to_csv(
        self, tmp_path, energy_profile: Profile, three_leads: List[Lead]
    ):
        output = tmp_path / "out.csv"
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(output),
            input_csvs=["ignored.csv"],
            importer=_FakeImporter(three_leads),  # type: ignore[arg-type]
        ).run()
        assert isinstance(result, LeadPipelineResult)
        assert result.success is True
        assert result.error == ""
        assert output.is_file()
        with open(output, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 3

    def test_per_stage_metrics_present(
        self, tmp_path, energy_profile: Profile, three_leads: List[Lead]
    ):
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter(three_leads),  # type: ignore[arg-type]
        ).run()
        names = [s.name for s in result.stages]
        assert names == ["import", "classify", "verify", "enrich", "dedupe", "export"]

    def test_duration_recorded(
        self, tmp_path, energy_profile: Profile, three_leads: List[Lead]
    ):
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter(three_leads),  # type: ignore[arg-type]
        ).run()
        assert result.duration_seconds >= 0.0


# ── LeadPipeline: stage ordering ───────────────────────────────────


class TestLeadPipelineStageOrdering:
    def test_stages_called_in_order(
        self, tmp_path, energy_profile: Profile, three_leads: List[Lead]
    ):
        events: List[str] = []

        class _RecorderImporter(_FakeImporter):
            def import_leads(self, csv_paths: List[str]) -> List[Lead]:  # type: ignore[override]
                events.append("import")
                return super().import_leads(csv_paths)

        class _RecorderKboReader(_FakeKboReader):
            def build_index(self, zip_path: str, targets: set, **kwargs: Any) -> Dict[str, _FakeKboRecord]:
                events.append("kbo_reader")
                return super().build_index(zip_path, targets, **kwargs)

        class _RecorderPappers(_FakeEnricher):
            def enrich(self, *args: Any) -> Dict[str, Any]:
                events.append("pappers")
                return super().enrich(*args)

        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_RecorderImporter(three_leads),  # type: ignore[arg-type]
            kbo_zip_path="/tmp/fake.zip",
            kbo_reader=_RecorderKboReader({}),  # type: ignore[arg-type]
            pappers=_RecorderPappers(),  # type: ignore[arg-type]
        ).run()
        # import fires once, kbo_reader fires once, pappers fires once per lead.
        # Check that all three happened, and that import came first.
        assert events[0] == "import"
        assert "kbo_reader" in events
        assert "pappers" in events
        assert events.index("import") < events.index("kbo_reader")
        assert events.index("kbo_reader") < events.index("pappers")


# ── LeadPipeline: verify stage ─────────────────────────────────────


class TestLeadPipelineVerifyStage:
    def test_fills_empty_fields_from_kbo_record(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789")
        reader = _FakeKboReader(
            {
                "BE0123456789": _FakeKboRecord(
                    denomination="ACME OFFICIAL",
                    address="Rue de la Loi 16",
                    municipality="Brussels",
                    zipcode="1000",
                    email="info@acme.test",
                    phone="+3212345678",
                    website="https://acme.test",
                )
            }
        )
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            kbo_zip_path="/tmp/fake.zip",
            kbo_reader=reader,  # type: ignore[arg-type]
        ).run()
        assert result.success
        verify_stage = next(s for s in result.stages if s.name == "verify")
        assert verify_stage.notes["index_size"] == 1
        assert verify_stage.notes["verified"] == 1
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        row = rows[0]
        assert row["Address"] == "Rue de la Loi 16"
        assert row["City"] == "Brussels"
        assert row["Postal code"] == "1000"
        assert row["Email"] == "info@acme.test"

    def test_does_not_overwrite_existing_email(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789", email="existing@acme.test")
        reader = _FakeKboReader(
            {"BE0123456789": _FakeKboRecord(email="kbo@acme.test")}
        )
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            kbo_zip_path="/tmp/fake.zip",
            kbo_reader=reader,  # type: ignore[arg-type]
        ).run()
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["Email"] == "existing@acme.test"

    def test_skipped_without_kbo_zip(
        self, tmp_path, energy_profile: Profile, three_leads: List[Lead]
    ):
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter(three_leads),  # type: ignore[arg-type]
        ).run()
        verify_stage = next(s for s in result.stages if s.name == "verify")
        assert verify_stage.notes.get("skipped") is True

    def test_inactive_and_not_found_counted(
        self, tmp_path, energy_profile: Profile
    ):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Beta", tva="0415678901"),
            Lead(company_name="Gamma", tva="0412345678"),
        ]
        reader = _FakeKboReader(
            {
                "BE0123456789": _FakeKboRecord(status="AC"),
                "BE0415678901": _FakeKboRecord(status="inactive"),
            }
        )
        verifier = _FakeKboVerifier({"BE0412345678": "not_found"})
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter(leads),  # type: ignore[arg-type]
            kbo_zip_path="/tmp/fake.zip",
            kbo_reader=reader,  # type: ignore[arg-type]
            kbo_verifier=verifier,
        ).run()
        verify_stage = next(s for s in result.stages if s.name == "verify")
        assert verify_stage.notes["verified"] == 1
        assert verify_stage.notes["inactive"] == 1
        assert verify_stage.notes["not_found"] == 1


# ── LeadPipeline: enrich stage ────────────────────────────────────


class TestLeadPipelineEnrichStage:
    def test_pappers_fills_empty_fields(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789")
        pappers = _FakeEnricher(
            {
                "first_name": "Jean",
                "last_name": "Dupont",
                "position": "CEO",
                "email": "jean@acme.test",
            }
        )
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            pappers=pappers,
        ).run()
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["First Name"] == "Jean"
        assert row["Last Name"] == "Dupont"
        assert row["Position"] == "CEO"
        assert row["Email"] == "jean@acme.test"

    def test_pappers_preserves_existing_fields(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(
            company_name="Acme",
            tva="0123456789",
            first_name="Existing",
            last_name="Person",
        )
        pappers = _FakeEnricher({"first_name": "New", "last_name": "Name"})
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            pappers=pappers,
        ).run()
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["First Name"] == "Existing"
        assert row["Last Name"] == "Person"

    def test_pappers_two_arg_signature_used(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789")
        pappers = _FakeEnricher({"email": "x@y.test"}, signature="two")
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            pappers=pappers,
        ).run()
        assert pappers.calls and len(pappers.calls[0]) == 2
        assert pappers.calls[0][0] == "BE0123456789"
        assert pappers.calls[0][1] == "Acme"

    def test_pappers_failure_recorded_not_raised(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789")
        pappers = _FakeEnricher(raises=True)
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            pappers=pappers,
        ).run()
        assert result.success
        enrich_stage = next(s for s in result.stages if s.name == "enrich")
        assert enrich_stage.errors
        assert "simulated network failure" in enrich_stage.errors[0]

    def test_pappers_none_skips_enrichment(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789")
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
            pappers=None,
            kbo_web=None,
        ).run()
        enrich_stage = next(s for s in result.stages if s.name == "enrich")
        assert enrich_stage.notes["enriched_count"] == 0


# ── LeadPipeline: classify stage ───────────────────────────────────


class TestLeadPipelineClassifyStage:
    def test_province_classifies_region_and_language(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="Acme", tva="0123456789", province="Hainaut")
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
        ).run()
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["Province"] == "Hainaut"
        assert row["Region"] == "Wallonia"
        assert row["Language"] == "FR"

    def test_existing_region_not_overwritten(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(
            company_name="Acme",
            tva="0123456789",
            province="Hainaut",
            region="Custom",
        )
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
        ).run()
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["Region"] == "Custom"

    def test_unknown_province_left_alone(
        self, tmp_path, energy_profile: Profile
    ):
        lead = Lead(company_name="X", tva="0123456789", province="Atlantis")
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([lead]),  # type: ignore[arg-type]
        ).run()
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            row = next(csv.DictReader(fh))
        assert row["Province"] == "Atlantis"
        assert row["Region"] == ""


# ── LeadPipeline: dedupe stage ─────────────────────────────────────


class TestLeadPipelineDedupeStage:
    def test_duplicate_tva_collapsed(
        self, tmp_path, energy_profile: Profile
    ):
        leads = [
            Lead(company_name="Acme", tva="0123456789", email="a@test.com"),
            Lead(company_name="Acme", tva="0123456789", email="b@test.com"),
        ]
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter(leads),  # type: ignore[arg-type]
        ).run()
        dedupe_stage = next(s for s in result.stages if s.name == "dedupe")
        assert dedupe_stage.notes["duplicates_removed"] == 1
        with open(tmp_path / "out.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1


# ── LeadPipeline: export stage ─────────────────────────────────────


class TestLeadPipelineExportStage:
    def test_csv_export_writes_zoho_header(
        self, tmp_path, energy_profile: Profile, three_leads: List[Lead]
    ):
        output = tmp_path / "out.csv"
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(output),
            input_csvs=["x.csv"],
            importer=_FakeImporter(three_leads),  # type: ignore[arg-type]
        ).run()
        assert result.success
        assert output.is_file()
        export_stage = next(s for s in result.stages if s.name == "export")
        assert export_stage.notes["format"] == "csv"


# ── LeadPipeline: error handling ───────────────────────────────────


class TestLeadPipelineErrorHandling:
    def test_empty_import_returns_failure(
        self, tmp_path, energy_profile: Profile
    ):
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FakeImporter([]),  # type: ignore[arg-type]
        ).run()
        assert result.success is False
        assert "import produced no leads" in result.error

    def test_importer_exception_recorded_but_pipeline_continues(
        self, tmp_path, energy_profile: Profile
    ):
        result = LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=_FailingImporter([]),  # type: ignore[arg-type]
        ).run()
        import_stage = next(s for s in result.stages if s.name == "import")
        assert import_stage.errors
        assert "simulated import failure" in import_stage.errors[0]
        assert result.success is False


# ── LeadPipeline: dependency injection ─────────────────────────────


class TestLeadPipelineDependencyInjection:
    def test_custom_importer_used(self, tmp_path, energy_profile: Profile):
        importer = _FakeImporter([Lead(company_name="Custom", tva="0123456789")])
        LeadPipeline(
            profile=energy_profile,
            output_path=str(tmp_path / "out.csv"),
            input_csvs=["x.csv"],
            importer=importer,  # type: ignore[arg-type]
        ).run()
        assert importer.calls == [["x.csv"]]


# ── LeadPipeline: convenience wrapper ─────────────────────────────


class TestRunPipelineWrapper:
    def test_loads_profile_by_name(self, tmp_path, three_leads: List[Lead]):
        # We use a known-good profile name and the default importer.
        # Use a non-existent CSV so import returns [] and we get a
        # structured failure — but the profile is loaded correctly.
        result = run_pipeline(
            profile_name="energy",
            input_csvs=[str(tmp_path / "missing.csv")],
            output_path=str(tmp_path / "out.csv"),
        )
        assert isinstance(result, LeadPipelineResult)
        assert result.profile == "energy"

    def test_unknown_profile_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_pipeline(
                profile_name="nonexistent",
                input_csvs=["x.csv"],
                output_path=str(tmp_path / "out.csv"),
            )


# ── LeadPipeline: CLI ──────────────────────────────────────────────


class TestLeadPipelineCLI:
    def test_cli_end_to_end(self, tmp_path):
        csv_path = tmp_path / "input.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["Company Name", "VAT Number", "Province"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Company Name": "Acme",
                    "VAT Number": "0123456789",
                    "Province": "Hainaut",
                }
            )
        output = tmp_path / "out.csv"
        env = {**dict(os.environ), "PYTHONPATH": "src"}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "reswip_leads.pipeline",
                "--profile",
                "energy",
                "--input",
                str(csv_path),
                "--output",
                str(output),
            ],
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert output.is_file()
        with open(output, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert rows[0]["Business Name"] == "Acme"
        assert rows[0]["Region"] == "Wallonia"


# ── Stage metrics dataclass ───────────────────────────────────────


class TestPipelineStageMetrics:
    def test_to_dict(self):
        m = PipelineStageMetrics(
            name="dedupe",
            input_count=10,
            output_count=7,
            errors=["one failure"],
            notes={"duplicates_removed": 3},
        )
        d = m.to_dict()
        assert d == {
            "name": "dedupe",
            "input_count": 10,
            "output_count": 7,
            "errors": ["one failure"],
            "notes": {"duplicates_removed": 3},
        }
