"""Tests for TVA normalization, Lead model, fields, and profile loading."""
import pytest
from reswip_leads.core.models import Lead, normalize_tva
from reswip_leads.core.fields import classify_province, classify_region, classify_language
from reswip_leads.core.profile import load_profile


# ── TVA Normalization ──────────────────────────────────────────────

class TestNormalizeTva:
    def test_plain_digits(self):
        assert normalize_tva("0123456789") == "BE0123456789"

    def test_with_be_prefix(self):
        assert normalize_tva("BE0123456789") == "BE0123456789"

    def test_with_dots(self):
        assert normalize_tva("012.345.678.9") == "BE0123456789"

    def test_with_spaces(self):
        assert normalize_tva("012 345 678 9") == "BE0123456789"

    def test_lowercase(self):
        assert normalize_tva("be0123456789") == "BE0123456789"

    def test_mixed_format(self):
        assert normalize_tva("BE 012.345.678.9") == "BE0123456789"

    def test_empty_string(self):
        assert normalize_tva("") == ""

    def test_none(self):
        assert normalize_tva(None) == ""

    def test_only_be(self):
        assert normalize_tva("BE") == ""

    def test_eleven_digits(self):
        assert normalize_tva("0415678901") == "BE0415678901"

    def test_preserves_existing_prefix(self):
        assert normalize_tva("BE0415678901") == "BE0415678901"


# ── Lead Model ─────────────────────────────────────────────────────

class TestLeadModel:
    def test_create_lead_minimal(self):
        lead = Lead(company_name="Test Corp", tva="0123456789")
        assert lead.company_name == "Test Corp"
        assert lead.tva == "BE0123456789"

    def test_tva_auto_normalized(self):
        lead = Lead(company_name="X", tva="012.345.678.9")
        assert lead.tva == "BE0123456789"

    def test_optional_contact_fields(self):
        lead = Lead(company_name="X", tva="0123456789")
        assert lead.first_name is None
        assert lead.last_name is None
        assert lead.position is None
        assert lead.email is None
        assert lead.phone is None
        assert lead.mobile is None

    def test_contact_fields_settable(self):
        lead = Lead(
            company_name="X",
            tva="0123456789",
            first_name="Jean",
            last_name="Dupont",
            position="CEO",
        )
        assert lead.first_name == "Jean"
        assert lead.last_name == "Dupont"
        assert lead.position == "CEO"

    def test_company_name_required(self):
        with pytest.raises(ValueError):
            Lead(company_name="", tva="0123456789")

    def test_company_name_whitespace_rejected(self):
        with pytest.raises(ValueError):
            Lead(company_name="   ", tva="0123456789")

    def test_to_dict(self):
        lead = Lead(company_name="Acme", tva="0123456789", city="Brussels")
        d = lead.to_dict()
        assert d["Company Name"] == "Acme"
        assert d["VAT Number"] == "BE0123456789"
        assert d["City"] == "Brussels"

    def test_from_dict(self):
        d = {
            "Company Name": "Acme",
            "VAT Number": "0123456789",
            "First Name": "Jean",
            "Last Name": "Dupont",
        }
        lead = Lead.from_dict(d)
        assert lead.company_name == "Acme"
        assert lead.tva == "BE0123456789"
        assert lead.first_name == "Jean"
        assert lead.last_name == "Dupont"

    def test_from_dict_normalizes_tva(self):
        d = {"Company Name": "X", "VAT Number": "012.345.678.9"}
        lead = Lead.from_dict(d)
        assert lead.tva == "BE0123456789"

    def test_source_tracking(self):
        lead = Lead(company_name="X", tva="0123456789", source="iQualif")
        assert lead.source == "iQualif"

    def test_province_region_language(self):
        lead = Lead(
            company_name="X",
            tva="0123456789",
            province="Hainaut",
            region="Wallonia",
            language="FR",
        )
        assert lead.province == "Hainaut"
        assert lead.region == "Wallonia"
        assert lead.language == "FR"


# ── Belgian Fields ─────────────────────────────────────────────────

class TestClassifyProvince:
    def test_hainaut(self):
        assert classify_province("Hainaut") == "Hainaut"

    def test_liege(self):
        assert classify_province("Liège") == "Liège"

    def test_namur(self):
        assert classify_province("Namur") == "Namur"

    def test_brabant_wallon(self):
        assert classify_province("Brabant wallon") == "Brabant wallon"

    def test_luxembourg(self):
        assert classify_province("Luxembourg") == "Luxembourg"

    def test_bruxelles(self):
        assert classify_province("Bruxelles") == "Bruxelles"

    def test_case_insensitive(self):
        assert classify_province("HAINAUT") == "Hainaut"
        assert classify_province("namur") == "Namur"

    def test_unknown_returns_empty(self):
        assert classify_province("Unknown") == ""
        assert classify_province("") == ""


class TestClassifyRegion:
    def test_wallonia(self):
        assert classify_region("Hainaut") == "Wallonia"
        assert classify_region("Liège") == "Wallonia"
        assert classify_region("Namur") == "Wallonia"
        assert classify_region("Luxembourg") == "Wallonia"
        assert classify_region("Brabant wallon") == "Wallonia"

    def test_brussels(self):
        assert classify_region("Bruxelles") == "Brussels"

    def test_flanders(self):
        assert classify_region("Antwerpen") == "Flanders"
        assert classify_region("Oost-Vlaanderen") == "Flanders"

    def test_unknown(self):
        assert classify_region("Unknown") == ""


class TestClassifyLanguage:
    def test_french(self):
        assert classify_language("Hainaut") == "FR"
        assert classify_language("Liège") == "FR"
        assert classify_language("Namur") == "FR"
        assert classify_language("Luxembourg") == "FR"
        assert classify_language("Brabant wallon") == "FR"
        assert classify_language("Bruxelles") == "FR"

    def test_dutch(self):
        assert classify_language("Antwerpen") == "NL"
        assert classify_language("Oost-Vlaanderen") == "NL"

    def test_unknown(self):
        assert classify_language("Unknown") == ""


# ── Profile Loading ────────────────────────────────────────────────

class TestProfileLoading:
    def test_load_energy_profile(self):
        profile = load_profile("energy")
        assert profile.name == "energy"
        assert "iqualif" in profile.sources

    def test_load_insurance_profile(self):
        profile = load_profile("insurance")
        assert profile.name == "insurance"

    def test_unknown_profile_raises(self):
        with pytest.raises(FileNotFoundError):
            load_profile("nonexistent")

    def test_profile_has_filters(self):
        profile = load_profile("energy")
        assert "energy" in profile.filters.get("industry", [])
