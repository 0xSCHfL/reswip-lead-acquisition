"""Tests for KBO verifier using KboZipReader."""
import csv
import io
import zipfile

import pytest
from reswip_leads.verification.kbo.verifier import KboVerifier
from reswip_leads.verification.kbo.zip_reader import KboRecord


def _build_kbo_zip(tmp_path, enterprises):
    """Build a minimal KBO ZIP for testing.

    Args:
        enterprises: list of dicts with keys: enterprise_number, status,
            denomination, zipcode, municipality, address, email, phone, website.
    """
    zip_path = tmp_path / "kbo_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # enterprise.csv
        ent_buf = io.StringIO()
        ent_writer = csv.DictWriter(
            ent_buf, fieldnames=["EnterpriseNumber", "Status", "JuridicalForm"]
        )
        ent_writer.writeheader()
        for ent in enterprises:
            ent_writer.writerow(
                {
                    "EnterpriseNumber": ent["enterprise_number"],
                    "Status": ent.get("status", "AC"),
                    "JuridicalForm": ent.get("juridical_form", "020"),
                }
            )
        zf.writestr("enterprise.csv", ent_buf.getvalue())

        # denomination.csv
        den_buf = io.StringIO()
        den_writer = csv.DictWriter(
            den_buf, fieldnames=["EntityNumber", "Denomination", "Language"]
        )
        den_writer.writeheader()
        for ent in enterprises:
            name = ent.get("denomination", "")
            if name:
                den_writer.writerow(
                    {
                        "EntityNumber": ent["enterprise_number"],
                        "Denomination": name,
                        "Language": "FR",
                    }
                )
        zf.writestr("denomination.csv", den_buf.getvalue())

        # address.csv
        addr_buf = io.StringIO()
        addr_writer = csv.DictWriter(
            addr_buf,
            fieldnames=[
                "EntityNumber", "TypeOfAddress", "StreetFR", "StreetNL",
                "HouseNumber", "Box", "Zipcode", "MunicipalityFR", "MunicipalityNL",
            ],
        )
        addr_writer.writeheader()
        for ent in enterprises:
            addr_writer.writerow(
                {
                    "EntityNumber": ent["enterprise_number"],
                    "TypeOfAddress": "REGO",
                    "StreetFR": ent.get("street", ""),
                    "StreetNL": "",
                    "HouseNumber": ent.get("house_number", ""),
                    "Box": "",
                    "Zipcode": ent.get("zipcode", ""),
                    "MunicipalityFR": ent.get("municipality", ""),
                    "MunicipalityNL": "",
                }
            )
        zf.writestr("address.csv", addr_buf.getvalue())

        # contact.csv
        contact_buf = io.StringIO()
        contact_writer = csv.DictWriter(
            contact_buf, fieldnames=["EntityNumber", "ContactType", "Value"]
        )
        contact_writer.writeheader()
        for ent in enterprises:
            if ent.get("email"):
                contact_writer.writerow(
                    {"EntityNumber": ent["enterprise_number"], "ContactType": "EMAIL", "Value": ent["email"]}
                )
            if ent.get("phone"):
                contact_writer.writerow(
                    {"EntityNumber": ent["enterprise_number"], "ContactType": "PHONE", "Value": ent["phone"]}
                )
            if ent.get("website"):
                contact_writer.writerow(
                    {"EntityNumber": ent["enterprise_number"], "ContactType": "WEB", "Value": ent["website"]}
                )
        zf.writestr("contact.csv", contact_buf.getvalue())

    return str(zip_path)


class TestKboVerifierWithZip:
    def test_verify_found(self, tmp_path):
        enterprises = [
            {
                "enterprise_number": "0412345678",
                "status": "AC",
                "denomination": "Acme Corp",
                "zipcode": "1000",
                "municipality": "Bruxelles",
                "street": "Rue de la Loi",
                "house_number": "16",
                "email": "info@acme.be",
                "phone": "+3221234567",
                "website": "https://acme.be",
            }
        ]
        zip_path = _build_kbo_zip(tmp_path, enterprises)
        verifier = KboVerifier(zip_path=zip_path)
        result = verifier.verify("BE0412345678")
        assert result["status"] == "verified"
        assert result["kbo_status"] == "AC"
        assert result["company_name"] == "Acme Corp"
        assert result["zipcode"] == "1000"
        assert result["municipality"] == "Bruxelles"
        assert result["email"] == "info@acme.be"
        assert result["phone"] == "+3221234567"
        assert result["website"] == "https://acme.be"
        assert result["is_active"] is True

    def test_verify_not_found(self, tmp_path):
        enterprises = [
            {"enterprise_number": "0412345678", "status": "AC", "denomination": "Acme Corp"},
        ]
        zip_path = _build_kbo_zip(tmp_path, enterprises)
        verifier = KboVerifier(zip_path=zip_path)
        result = verifier.verify("BE0999999999")
        assert result["status"] == "not_found"
        assert result["is_active"] is False
        assert result["company_name"] == ""

    def test_verify_inactive(self, tmp_path):
        enterprises = [
            {"enterprise_number": "0412345678", "status": "INACTIVE", "denomination": "Dead Corp"},
        ]
        zip_path = _build_kbo_zip(tmp_path, enterprises)
        verifier = KboVerifier(zip_path=zip_path)
        result = verifier.verify("BE0412345678")
        assert result["status"] == "inactive"
        assert result["kbo_status"] == "INACTIVE"
        assert result["is_active"] is False

    def test_verify_no_zip_returns_not_found(self):
        verifier = KboVerifier()
        result = verifier.verify("BE0412345678")
        assert result["status"] == "not_found"

    def test_verify_zip_override(self, tmp_path):
        enterprises = [
            {"enterprise_number": "0412345678", "status": "AC", "denomination": "Override Corp"},
        ]
        zip_path = _build_kbo_zip(tmp_path, enterprises)
        verifier = KboVerifier()
        result = verifier.verify("BE0412345678", zip_path=str(zip_path))
        assert result["status"] == "verified"
        assert result["company_name"] == "Override Corp"

    def test_verify_batch(self, tmp_path):
        enterprises = [
            {"enterprise_number": "0412345678", "status": "AC", "denomination": "Acme"},
            {"enterprise_number": "0498765432", "status": "AC", "denomination": "Beta"},
        ]
        zip_path = _build_kbo_zip(tmp_path, enterprises)
        verifier = KboVerifier(zip_path=zip_path)
        results = verifier.verify_batch(["BE0412345678", "BE0498765432", "BE0111111111"])
        assert len(results) == 3
        assert results["BE0412345678"]["status"] == "verified"
        assert results["BE0498765432"]["status"] == "verified"
        assert results["BE0111111111"]["status"] == "not_found"

    def test_verify_batch_empty(self):
        verifier = KboVerifier()
        results = verifier.verify_batch([])
        assert results == {}

    def test_verify_preserves_all_fields(self, tmp_path):
        enterprises = [
            {
                "enterprise_number": "0412345678",
                "status": "AC",
                "denomination": "Full Corp",
                "zipcode": "2000",
                "municipality": "Antwerpen",
                "street": "Meir",
                "house_number": "10",
                "email": "test@full.be",
                "phone": "+3231234567",
                "website": "https://full.be",
            }
        ]
        zip_path = _build_kbo_zip(tmp_path, enterprises)
        verifier = KboVerifier(zip_path=zip_path)
        result = verifier.verify("BE0412345678")
        assert result["enterprise_number"] == "0412345678"
        assert result["address"] == "Meir 10"
        assert result["zipcode"] == "2000"
        assert result["municipality"] == "Antwerpen"
