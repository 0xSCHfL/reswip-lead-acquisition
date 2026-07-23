"""Tests for Zoho CRM CSV/XLSX export."""
import csv
import os
import tempfile
import pytest
from reswip_leads.core.models import Lead
from reswip_leads.core.profile import Profile
from reswip_leads.exports.zoho import export_csv, export_xlsx, ZOHO_COLUMNS


class TestZohoColumnOrder:
    def test_column_count(self):
        assert len(ZOHO_COLUMNS) == 21

    def test_expected_columns(self):
        expected = [
            "Business Name",
            "TVA Number",
            "Address",
            "Postal code",
            "City",
            "Province",
            "Region",
            "DB_Region",
            "Language",
            "Phone",
            "Mobile",
            "Email",
            "Website",
            "First Name",
            "Last Name",
            "Position",
            "Contact First Name",
            "Contact Last Name",
            "Category",
            "Organization",
            "Lead Source",
        ]
        assert ZOHO_COLUMNS == expected


class TestExportCsv:
    def test_creates_file(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        assert out.exists()

    def test_header_matches_zoho_columns(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == ZOHO_COLUMNS

    def test_single_lead_row(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789", city="Brussels")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["Business Name"] == "Acme"
        assert rows[0]["TVA Number"] == "BE0123456789"
        assert rows[0]["City"] == "Brussels"

    def test_empty_leads(self, tmp_path):
        out = tmp_path / "out.csv"
        export_csv([], str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == ZOHO_COLUMNS

    def test_empty_contact_fields_preserved(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["First Name"] == ""
        assert row["Last Name"] == ""
        assert row["Position"] == ""
        assert row["Email"] == ""
        assert row["Phone"] == ""
        assert row["Mobile"] == ""

    def test_multiple_leads(self, tmp_path):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Beta", tva="0415678901"),
        ]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2


class TestProfileDefaults:
    def test_energy_profile_defaults(self, tmp_path):
        profile = Profile(
            name="energy",
            extra={"organization": "Reswip Prospect", "lead_source": "Energy Prospect"},
        )
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out), profile=profile)
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Organization"] == "Reswip Prospect"
        assert row["Lead Source"] == "Energy Prospect"

    def test_insurance_profile_defaults(self, tmp_path):
        profile = Profile(
            name="insurance",
            extra={"organization": "Reswip Insurance", "lead_source": "Insurance Prospect"},
        )
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out), profile=profile)
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Organization"] == "Reswip Insurance"
        assert row["Lead Source"] == "Insurance Prospect"

    def test_no_profile_defaults_empty(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Organization"] == ""
        assert row["Lead Source"] == ""


class TestContactFirstLast:
    def test_contact_first_last_from_director(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                first_name="Jean",
                last_name="Dupont",
            )
        ]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Contact First Name"] == "Jean"
        assert row["Contact Last Name"] == "Dupont"


class TestCategoryExport:
    def test_category_exported(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789", category="Energy")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Category"] == "Energy"

    def test_empty_category_exported_empty(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_csv(leads, str(out))
        with open(out, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["Category"] == ""


class TestExportXlsx:
    def test_creates_file_if_openpyxl(self, tmp_path):
        try:
            import openpyxl  # noqa: F401
        except (ImportError, Exception):
            pytest.skip("openpyxl not available")
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.xlsx"
        export_xlsx(leads, str(out))
        assert out.exists()

    def test_xlsx_header(self, tmp_path):
        try:
            import openpyxl  # noqa: F401
        except (ImportError, Exception):
            pytest.skip("openpyxl not available")
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.xlsx"
        export_xlsx(leads, str(out))
        wb = openpyxl.load_workbook(str(out))
        ws = wb.active
        header = [cell.value for cell in ws[1]]
        assert header == ZOHO_COLUMNS
