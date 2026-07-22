"""Tests for adapter interfaces and contracts."""
import pytest
from reswip_leads.sources.iqualif.importer import IQualifImporter
from reswip_leads.verification.kbo.verifier import KboVerifier
from reswip_leads.enrichment.pappers import PappersEnricher
from reswip_leads.enrichment.kbo_web import KboWebEnricher


class TestIQualifImporterContract:
    def test_has_import_method(self):
        assert callable(getattr(IQualifImporter, "import_leads", None))

    def test_has_index_method(self):
        assert callable(getattr(IQualifImporter, "build_index", None))

    def test_import_leads_returns_list(self):
        importer = IQualifImporter()
        result = importer.import_leads([])
        assert isinstance(result, list)


class TestKboVerifierContract:
    def test_has_verify_method(self):
        assert callable(getattr(KboVerifier, "verify", None))

    def test_verify_returns_dict(self):
        verifier = KboVerifier()
        result = verifier.verify("BE0123456789")
        assert isinstance(result, dict)

    def test_verify_returns_status(self):
        verifier = KboVerifier()
        result = verifier.verify("BE0123456789")
        assert "status" in result


class TestPappersEnricherContract:
    def test_has_enrich_method(self):
        assert callable(getattr(PappersEnricher, "enrich", None))

    def test_enrich_returns_dict(self):
        enricher = PappersEnricher()
        result = enricher.enrich("BE0123456789", "Test Corp")
        assert isinstance(result, dict)


class TestKboWebEnricherContract:
    def test_has_enrich_method(self):
        assert callable(getattr(KboWebEnricher, "enrich", None))

    def test_enrich_returns_dict(self):
        enricher = KboWebEnricher()
        result = enricher.enrich("BE0123456789")
        assert isinstance(result, dict)
