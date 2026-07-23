"""Tests for Zoho CRM CSV/XLSX export."""
import csv
import os
import tempfile
import pytest
from reswip_leads.core.models import Lead
from reswip_leads.core.profile import Profile
from reswip_leads.exports.zoho import (
    export_csv,
    export_energy_csv,
    export_xlsx,
    ZOHO_COLUMNS,
    ENERGY_ZOHO_COLUMNS,
    EnergyExportMetrics,
    energy_lead_to_row,
)


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


class TestEnergyZohoColumns:
    def test_column_count(self):
        assert len(ENERGY_ZOHO_COLUMNS) == 24

    def test_expected_columns(self):
        expected = [
            "Sector of Activity",
            "Business Name",
            "Postal code",
            "City",
            "Region",
            "Province",
            "Address",
            "Phone",
            "Mobile",
            "Fax",
            "webite",
            "Email",
            "TVA Number",
            "First Name",
            "Last Name",
            "Position",
            "Email 1",
            "Contact First Name",
            "Contact Last Name",
            "PreLead Prospect Name",
            "DB_Region",
            "Language",
            "Organization",
            "Lead Source",
        ]
        assert ENERGY_ZOHO_COLUMNS == expected

    def test_prelead_prospect_name_position(self):
        idx = ENERGY_ZOHO_COLUMNS.index("PreLead Prospect Name")
        assert ENERGY_ZOHO_COLUMNS[idx - 1] == "Contact Last Name"
        assert ENERGY_ZOHO_COLUMNS[idx + 1] == "DB_Region"


class TestEnergyPreLeadName:
    def test_first_and_last_name(self):
        lead = Lead(
            company_name="Acme",
            tva="0123456789",
            first_name="Pierre",
            last_name="DEBAISIEUX",
        )
        row = energy_lead_to_row(lead)
        assert row["PreLead Prospect Name"] == "Pierre DEBAISIEUX"

    def test_last_name_uppercased(self):
        lead = Lead(
            company_name="Acme",
            tva="0123456789",
            first_name="Jean",
            last_name="dupont",
        )
        row = energy_lead_to_row(lead)
        assert row["PreLead Prospect Name"] == "Jean DUPONT"

    def test_only_last_name(self):
        lead = Lead(
            company_name="Acme",
            tva="0123456789",
            last_name="Smith",
        )
        row = energy_lead_to_row(lead)
        assert row["PreLead Prospect Name"] == "SMITH"

    def test_only_first_name(self):
        lead = Lead(
            company_name="Acme",
            tva="0123456789",
            first_name="Jean",
        )
        row = energy_lead_to_row(lead)
        assert row["PreLead Prospect Name"] == "Jean"

    def test_fallback_to_business_name_when_no_contact(self):
        lead = Lead(company_name="Acme BV", tva="0123456789")
        row = energy_lead_to_row(lead)
        assert row["PreLead Prospect Name"] == "Acme BV"

    def test_never_empty(self):
        lead = Lead(company_name="Acme", tva="0123456789")
        row = energy_lead_to_row(lead)
        assert row["PreLead Prospect Name"] != ""


class TestEnergyExportCsv:
    def test_header_matches_energy_columns(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader)
        assert header == ENERGY_ZOHO_COLUMNS

    def test_column_count_24(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader)
        assert len(header) == 24

    def test_semicolon_delimiter(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            content = f.read()
        assert ";" in content

    def test_utf8_bom(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, "rb") as f:
            bom = f.read(3)
        assert bom == b"\xef\xbb\xbf"

    def test_last_name_uppercased_in_output(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                first_name="Pierre",
                last_name="deBAISIEUX",
            )
        ]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["Last Name"] == "DEBAISIEUX"
        assert row["Contact Last Name"] == "DEBAISIEUX"

    def test_email_1_equals_email(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                email="test@example.com",
            )
        ]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["Email"] == "test@example.com"
        assert row["Email 1"] == "test@example.com"

    def test_sector_of_activity_from_category(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                category="Energy",
            )
        ]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["Sector of Activity"] == "Energy"

    def test_webite_column_exists(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                website="https://acme.be",
            )
        ]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["webite"] == "https://acme.be"

    def test_prelead_name_with_contact(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                first_name="Pierre",
                last_name="DEBAISIEUX",
            )
        ]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["PreLead Prospect Name"] == "Pierre DEBAISIEUX"

    def test_prelead_name_fallback_to_business_name(self, tmp_path):
        leads = [Lead(company_name="Acme BV", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["PreLead Prospect Name"] == "Acme BV"

    def test_no_extra_columns(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert set(row.keys()) == set(ENERGY_ZOHO_COLUMNS)

    def test_empty_fields_preserved(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["First Name"] == ""
        assert row["Last Name"] == ""
        assert row["Email"] == ""
        assert row["Phone"] == ""
        assert row["Mobile"] == ""
        assert row["Fax"] == ""
        assert row["webite"] == ""


class TestEnergyExportMetrics:
    def test_fallback_count(self, tmp_path):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(
                company_name="Beta",
                tva="0415678901",
                first_name="Jean",
                last_name="Dupont",
            ),
        ]
        out = tmp_path / "out.csv"
        _, metrics = export_energy_csv(leads, str(out))
        assert metrics.total_rows == 2
        assert metrics.business_name_fallbacks == 1

    def test_all_fallbacks(self, tmp_path):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Beta", tva="0415678901"),
        ]
        out = tmp_path / "out.csv"
        _, metrics = export_energy_csv(leads, str(out))
        assert metrics.business_name_fallbacks == 2

    def test_no_fallbacks_when_names_present(self, tmp_path):
        leads = [
            Lead(
                company_name="Acme",
                tva="0123456789",
                first_name="Jean",
                last_name="Dupont",
            ),
        ]
        out = tmp_path / "out.csv"
        _, metrics = export_energy_csv(leads, str(out))
        assert metrics.business_name_fallbacks == 0


class TestEnergyProfileDefaults:
    def test_energy_profile_defaults(self, tmp_path):
        profile = Profile(
            name="energy",
            extra={"organization": "Reswip Prospect", "lead_source": "Energy Prospect"},
        )
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out), profile=profile)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
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
        export_energy_csv(leads, str(out), profile=profile)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["Organization"] == "Reswip Insurance"
        assert row["Lead Source"] == "Insurance Prospect"

    def test_no_profile_defaults_empty(self, tmp_path):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        out = tmp_path / "out.csv"
        export_energy_csv(leads, str(out))
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            row = next(reader)
        assert row["Organization"] == ""
        assert row["Lead Source"] == ""
