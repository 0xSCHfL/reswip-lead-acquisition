"""Tests for TVA-based deduplication."""
import pytest
from reswip_leads.core.models import Lead
from reswip_leads.deduplication.dedupe import deduplicate, DedupeResult


class TestDeduplicateBasic:
    def test_single_lead_unchanged(self):
        leads = [Lead(company_name="Acme", tva="0123456789")]
        result = deduplicate(leads)
        assert result.output_count == 1
        assert result.input_count == 1
        assert len(result.duplicates) == 0

    def test_empty_input(self):
        result = deduplicate([])
        assert result.output_count == 0
        assert result.input_count == 0

    def test_two_unique_leads(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Beta Corp", tva="0415678901"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 2
        assert len(result.duplicates) == 0


class TestDeduplicateSameTva:
    def test_exact_duplicates(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", email="a@test.com"),
            Lead(company_name="Acme", tva="0123456789", email="a@test.com"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1
        assert len(result.duplicates) == 1
        assert result.duplicates[0] == "BE0123456789"

    def test_preserves_first_record(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", email="first@test.com"),
            Lead(company_name="Acme", tva="0123456789", email="second@test.com"),
        ]
        result = deduplicate(leads)
        assert len(result.leads) == 1
        assert result.leads[0].email == "first@test.com"


class TestDeduplicateFormatting:
    def test_dots_vs_no_dots(self):
        leads = [
            Lead(company_name="Acme", tva="012.345.678.9"),
            Lead(company_name="Acme", tva="0123456789"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1

    def test_with_be_prefix(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="BE0123456789"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1

    def test_lowercase(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="be0123456789"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1

    def test_mixed_formatting(self):
        leads = [
            Lead(company_name="Acme", tva="012 345 678 9"),
            Lead(company_name="Acme", tva="BE012.345.678.9"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1


class TestMergeMissingFields:
    def test_merge_email(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", email="contact@acme.com"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].email == "contact@acme.com"

    def test_merge_phone(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", phone="+3221234567"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].phone == "+3221234567"

    def test_merge_mobile(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", mobile="+32471234567"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].mobile == "+32471234567"

    def test_merge_website(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", website="https://acme.com"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].website == "https://acme.com"

    def test_merge_address(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", address="Rue de la Loi 16"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].address == "Rue de la Loi 16"

    def test_merge_city(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", city="Brussels"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].city == "Brussels"

    def test_merge_province(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", province="Hainaut"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].province == "Hainaut"

    def test_merge_director_fields(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789",
                 first_name="Jean", last_name="Dupont", position="CEO"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].first_name == "Jean"
        assert result.leads[0].last_name == "Dupont"
        assert result.leads[0].position == "CEO"


class TestNeverOverwrite:
    def test_preserve_existing_email(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", email="original@test.com"),
            Lead(company_name="Acme", tva="0123456789", email="other@test.com"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].email == "original@test.com"

    def test_preserve_existing_phone(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", phone="+3221111111"),
            Lead(company_name="Acme", tva="0123456789", phone="+3222222222"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].phone == "+3221111111"

    def test_preserve_existing_name(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789",
                 first_name="Original", last_name="Name"),
            Lead(company_name="Acme", tva="0123456789",
                 first_name="Other", last_name="Person"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].first_name == "Original"
        assert result.leads[0].last_name == "Name"

    def test_empty_does_not_overwrite(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", email="real@test.com"),
            Lead(company_name="Acme", tva="0123456789"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].email == "real@test.com"


class TestBranchRecords:
    def test_different_cities_keep_both(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", city="Brussels", address="Addr 1"),
            Lead(company_name="Acme", tva="0123456789", city="Antwerp", address="Addr 2"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 2
        assert len(result.duplicates) == 0

    def test_same_city_different_address_keeps_both(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", city="Brussels", address="Rue 1"),
            Lead(company_name="Acme", tva="0123456789", city="Brussels", address="Rue 2"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 2

    def test_same_address_merges(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", address="Rue de la Loi 16", city="Brussels"),
            Lead(company_name="Acme", tva="0123456789", address="Rue de la Loi 16", city="Brussels"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1


class TestBlankInvalidTva:
    def test_blank_tva_not_deduped(self):
        leads = [
            Lead(company_name="Acme"),
            Lead(company_name="Acme"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 2
        assert len(result.duplicates) == 0

    def test_one_blank_one_valid(self):
        leads = [
            Lead(company_name="Acme"),
            Lead(company_name="Beta", tva="0123456789"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 2

    def test_blank_tva_preserved(self):
        leads = [
            Lead(company_name="Acme"),
            Lead(company_name="Acme"),
        ]
        result = deduplicate(leads)
        assert result.leads[0].tva == ""
        assert result.leads[1].tva == ""


class TestDedupeResult:
    def test_result_fields(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789"),
        ]
        result = deduplicate(leads)
        assert isinstance(result, DedupeResult)
        assert result.input_count == 2
        assert result.output_count == 1
        assert len(result.leads) == 1
        assert "BE0123456789" in result.duplicates

    def test_multiple_duplicates(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Beta", tva="0415678901"),
            Lead(company_name="Beta", tva="0415678901"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 2
        assert len(result.duplicates) == 2
        assert "BE0123456789" in result.duplicates
        assert "BE0415678901" in result.duplicates


class TestMergeMultipleSources:
    def test_three_rows_merge(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789", email="a@test.com"),
            Lead(company_name="Acme", tva="0123456789", phone="+3221234567"),
            Lead(company_name="Acme", tva="0123456789", website="https://acme.com"),
        ]
        result = deduplicate(leads)
        assert result.output_count == 1
        lead = result.leads[0]
        assert lead.email == "a@test.com"
        assert lead.phone == "+3221234567"
        assert lead.website == "https://acme.com"

    def test_merge_from_multiple_duplicates(self):
        leads = [
            Lead(company_name="Acme", tva="0123456789"),
            Lead(company_name="Acme", tva="0123456789", email="a@test.com", phone="+3221111111"),
            Lead(company_name="Acme", tva="0123456789", mobile="+32471111111", city="Brussels"),
        ]
        result = deduplicate(leads)
        lead = result.leads[0]
        assert lead.email == "a@test.com"
        assert lead.phone == "+3221111111"
        assert lead.mobile == "+32471111111"
        assert lead.city == "Brussels"
