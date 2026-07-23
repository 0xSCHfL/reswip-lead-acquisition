"""Tests for email recheck sources.

Covers:
* ``TestEmailCandidate`` — dataclass construction.
* ``TestIsValidEmail`` — rejection and acceptance rules.
* ``TestBaseEmailSource`` — ABC interface.
* ``TestKboZipEmailSource`` — offline KBO ZIP email extraction.
* ``TestKboEmailSource`` — KBO pub page email extraction.
* ``TestPappersEmailSource`` — Pappers email extraction.
* ``TestWebsiteEmailSource`` — website email extraction.
"""
from __future__ import annotations

import csv
from typing import Dict, Optional, Set
from unittest.mock import MagicMock, patch

import pytest

from reswip_leads.enrichment.email_sources import (
    BaseEmailSource,
    EmailCandidate,
    KboEmailSource,
    KboZipEmailSource,
    PappersEmailSource,
    WebsiteEmailSource,
    _is_valid_email,
)
from reswip_leads.enrichment.email_recheck import EmailRecheckEnricher, _process_csv, _build_sources
from reswip_leads.verification.kbo.zip_reader import KboRecord, KboZipReader


# ── Helpers ─────────────────────────────────────────────────────────


def _make_record(email: str = "", enterprise_number: str = "0123456789") -> KboRecord:
    return KboRecord(enterprise_number=enterprise_number, email=email)


def _make_reader(records: Dict[str, KboRecord]) -> MagicMock:
    reader = MagicMock(spec=KboZipReader)
    reader.build_index.return_value = records
    return reader


# ── EmailCandidate ─────────────────────────────────────────────────


class TestEmailCandidate:
    def test_email_candidate_fields(self):
        candidate = EmailCandidate(
            email="test@example.be",
            source="kbo_zip",
            confidence="High",
            source_url="https://example.be",
        )
        assert candidate.email == "test@example.be"
        assert candidate.source == "kbo_zip"
        assert candidate.confidence == "High"
        assert candidate.source_url == "https://example.be"

    def test_email_candidate_defaults(self):
        candidate = EmailCandidate(
            email="test@example.be",
            source="kbo_zip",
            confidence="High",
        )
        assert candidate.source_url == ""


# ── _is_valid_email ────────────────────────────────────────────────


class TestIsValidEmail:
    def test_valid_generic_email(self):
        assert _is_valid_email("user@example.org") is True

    def test_rejects_noreply(self):
        assert _is_valid_email("noreply@company.be") is False

    def test_rejects_no_reply(self):
        assert _is_valid_email("no-reply@company.be") is False

    def test_rejects_donotreply(self):
        assert _is_valid_email("donotreply@company.be") is False

    def test_rejects_example_com(self):
        assert _is_valid_email("user@example.com") is False

    def test_rejects_test_com(self):
        assert _is_valid_email("user@test.com") is False

    def test_rejects_localhost(self):
        assert _is_valid_email("user@localhost") is False

    def test_rejects_pappers(self):
        assert _is_valid_email("info@pappers.be") is False

    def test_rejects_kbo_pub(self):
        assert _is_valid_email("info@kbopub.economie.fgov.be") is False

    def test_rejects_social_media_google(self):
        assert _is_valid_email("user@google.com") is False

    def test_rejects_social_media_facebook(self):
        assert _is_valid_email("user@facebook.com") is False

    def test_rejects_social_media_linkedin(self):
        assert _is_valid_email("user@linkedin.com") is False

    def test_rejects_empty(self):
        assert _is_valid_email("") is False

    def test_rejects_no_at_sign(self):
        assert _is_valid_email("usercompany.be") is False

    def test_rejects_too_long(self):
        assert _is_valid_email("a" * 250 + "@example.org") is False


# ── BaseEmailSource ────────────────────────────────────────────────


class TestBaseEmailSource:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseEmailSource()

    def test_find_email_signature(self):
        import inspect

        sig = inspect.signature(BaseEmailSource.find_email)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "tva" in params
        assert "company_name" in params
        assert "website_url" in params
        assert "proxy" in params


# ── KboZipEmailSource ──────────────────────────────────────────────


class TestKboZipEmailSource:
    def test_finds_email_from_zip(self):
        record = _make_record(email="info@example.be", enterprise_number="0123456789")
        reader = _make_reader({"0123456789": record})
        source = KboZipEmailSource(reader, "/tmp/kbo.zip")

        result = source.find_email(tva="BE0123456789", company_name="Test NV")

        assert result is not None
        assert isinstance(result, EmailCandidate)
        assert result.email == "info@example.be"
        assert result.source == "kbo_zip"
        assert result.confidence == "High"

    def test_returns_none_when_no_email(self):
        record = _make_record(email="", enterprise_number="0123456789")
        reader = _make_reader({"0123456789": record})
        source = KboZipEmailSource(reader, "/tmp/kbo.zip")

        result = source.find_email(tva="BE0123456789")

        assert result is None

    def test_returns_none_when_tva_missing(self):
        reader = _make_reader({})
        source = KboZipEmailSource(reader, "/tmp/kbo.zip")

        result = source.find_email(tva="")

        assert result is None

    def test_high_confidence(self):
        record = _make_record(email="test@example.be")
        reader = _make_reader({"0123456789": record})
        source = KboZipEmailSource(reader, "/tmp/kbo.zip")

        result = source.find_email(tva="BE0123456789")

        assert result is not None
        assert result.confidence == "High"

    def test_source_name_is_kbo_zip(self):
        record = _make_record(email="test@example.be")
        reader = _make_reader({"0123456789": record})
        source = KboZipEmailSource(reader, "/tmp/kbo.zip")

        result = source.find_email(tva="BE0123456789")

        assert result is not None
        assert result.source == "kbo_zip"

    def test_no_network_request(self):
        record = _make_record(email="test@example.be")
        reader = _make_reader({"0123456789": record})
        source = KboZipEmailSource(reader, "/tmp/kbo.zip")

        source.find_email(tva="BE0123456789")

        reader.build_index.assert_called_once()
        call_args = reader.build_index.call_args
        assert call_args[0][0] == "/tmp/kbo.zip"


# ── KboEmailSource ────────────────────────────────────────────────


class TestKboEmailSource:
    def test_finds_email_from_kbo_page(self):
        html = '<html><body><a href="mailto:contact@acme.be">Contact</a></body></html>'
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is not None
        assert result.email == "contact@acme.be"
        assert result.source == "kbo"
        assert result.confidence == "Medium"

    def test_returns_none_when_no_email(self):
        html = "<html><body>No email here</body></html>"
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is None

    def test_returns_none_for_no_data_page(self):
        html = "<html><body>Geen gegevens opgenomen in KBO</body></html>"
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is None

    def test_handles_network_error(self):
        session = MagicMock()
        session.get.side_effect = ConnectionError("no network")

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is None

    def test_handles_non_200_status(self):
        resp = MagicMock(status_code=404, text="Not Found")
        session = MagicMock()
        session.get.return_value = resp

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is None

    def test_medium_confidence(self):
        html = '<a href="mailto:info@acme.be">Info</a>'
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is not None
        assert result.confidence == "Medium"

    def test_source_name_is_kbo(self):
        html = '<a href="mailto:x@acme.be">X</a>'
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = KboEmailSource()
        src._session = session
        result = src.find_email(tva="BE0123456789")

        assert result is not None
        assert result.source == "kbo"

    def test_returns_none_when_tva_missing(self):
        src = KboEmailSource()
        result = src.find_email(tva="")

        assert result is None


# ── WebsiteEmailSource ────────────────────────────────────────────


class TestWebsiteEmailSource:
    def test_finds_email_from_website(self) -> None:
        html = '<html><body><a href="mailto:contact@acme.be">Contact</a></body></html>'
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.content = html.encode()
        resp.headers = {"Content-Type": "text/html"}

        with patch("reswip_leads.enrichment.email_sources.requests") as mock_req:
            mock_sess = MagicMock()
            mock_sess.get.return_value = resp
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is not None
        assert result.email == "contact@acme.be"
        assert result.source == "website"
        assert result.source_url == "https://acme.be"

    def test_playwright_fallback_on_spa(self) -> None:
        spa_html = '<html><body><div id="app"></div></body></html>'
        resp = MagicMock()
        resp.status_code = 200
        resp.text = spa_html
        resp.content = spa_html.encode()
        resp.headers = {"Content-Type": "text/html"}

        rendered_html = (
            '<html><body><div id="app">'
            '<a href="mailto:hello@acme.be">Email us</a>'
            "</div></body></html>"
        )

        mock_pw_page = MagicMock()
        mock_pw_page.content.return_value = rendered_html
        mock_pw_page.goto = MagicMock()
        mock_pw_context = MagicMock()
        mock_pw_context.new_page.return_value = mock_pw_page
        mock_pw_browser = MagicMock()
        mock_pw_browser.new_context.return_value = mock_pw_context
        mock_pw_browser.__enter__ = MagicMock(return_value=mock_pw_browser)
        mock_pw_browser.__exit__ = MagicMock(return_value=False)

        # _pw_playwright() returns sync_playwright; sync_playwright() is the CM
        mock_sync_pw = MagicMock()
        mock_pw_cm = mock_sync_pw.return_value
        mock_pw_cm.chromium.launch.return_value = mock_pw_browser
        mock_pw_cm.__enter__ = MagicMock(return_value=mock_pw_cm)
        mock_pw_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch("reswip_leads.enrichment.email_sources.requests") as mock_req,
            patch(
                "reswip_leads.enrichment.email_sources._pw_playwright",
                mock_sync_pw,
            ),
        ):
            mock_sess = MagicMock()
            mock_sess.get.return_value = resp
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is not None
        assert result.email == "hello@acme.be"
        assert result.source == "website"

    def test_returns_none_when_no_website(self) -> None:
        src = WebsiteEmailSource()
        assert src.find_email(tva="BE0123456789", website_url="") is None

    def test_returns_none_when_no_email(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "<html><body>No email here</body></html>"
        resp.content = b"<html><body>No email here</body></html>"
        resp.headers = {"Content-Type": "text/html"}

        with patch("reswip_leads.enrichment.email_sources.requests") as mock_req:
            mock_sess = MagicMock()
            mock_sess.get.return_value = resp
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is None

    def test_handles_network_error(self) -> None:
        with patch("reswip_leads.enrichment.email_sources.requests") as mock_req:
            mock_sess = MagicMock()
            mock_sess.get.side_effect = ConnectionError("network down")
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is None

    def test_medium_confidence_for_info(self) -> None:
        html = '<html><body><a href="mailto:info@acme.be">Info</a></body></html>'
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.content = html.encode()
        resp.headers = {"Content-Type": "text/html"}

        with patch("reswip_leads.enrichment.email_sources.requests") as mock_req:
            mock_sess = MagicMock()
            mock_sess.get.return_value = resp
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is not None
        assert result.email == "info@acme.be"
        assert result.confidence == "Medium"

    def test_low_confidence_for_other(self) -> None:
        html = '<html><body><a href="mailto:sales@acme.be">Sales</a></body></html>'
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.content = html.encode()
        resp.headers = {"Content-Type": "text/html"}

        with patch("reswip_leads.enrichment.email_sources.requests") as mock_req:
            mock_sess = MagicMock()
            mock_sess.get.return_value = resp
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is not None
        assert result.email == "sales@acme.be"
        assert result.confidence == "Low"

    def test_source_name_is_website(self) -> None:
        html = '<html><body><a href="mailto:x@acme.be">X</a></body></html>'
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.content = html.encode()
        resp.headers = {"Content-Type": "text/html"}

        with patch("reswip_leads.enrichment.email_sources.requests") as mock_req:
            mock_sess = MagicMock()
            mock_sess.get.return_value = resp
            mock_req.Session.return_value = mock_sess

            src = WebsiteEmailSource()
            result = src.find_email(
                tva="BE0123456789",
                website_url="https://acme.be",
            )

        assert result is not None
        assert result.source == "website"


# ── PappersEmailSource ─────────────────────────────────────────────


class TestPappersEmailSource:
    def test_finds_email_from_pappers(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        html = '<html><body><a href="mailto:contact@acme.be">Contact</a></body></html>'
        resp = MagicMock(status_code=200, text=html)

        session = MagicMock()
        session.get.return_value = resp

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is not None
        assert result.email == "contact@acme.be"
        assert result.source == "pappers"
        assert result.confidence == "Low"

    def test_decodes_cloudflare_email(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        # Encode "test@acme.be" using CF email protection: key = hex('t') = 0x74
        key = 0x74
        raw = "test@acme.be"
        encoded = f"{key:02x}"
        for ch in raw:
            encoded += f"{ord(ch) ^ key:02x}"

        # pappers.py CF_EMAIL_RE matches /cdn-cgi/l/email-protection#<hex>
        html = (
            '<a href="/cdn-cgi/l/email-protection#' + encoded + '">'
            "[email&#160;protected]</a>"
        )
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is not None
        assert result.email == "test@acme.be"

    def test_returns_none_when_no_email(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        html = "<html><body><p>No email here</p></body></html>"
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is None

    def test_handles_network_error(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        session = MagicMock()
        session.get.side_effect = ConnectionError("no network")

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is None

    def test_handles_non_200_status(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        resp = MagicMock(status_code=404, text="Not Found")
        session = MagicMock()
        session.get.return_value = resp

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is None

    def test_low_confidence(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        html = '<a href="mailto:hi@acme.be">hi</a>'
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is not None
        assert result.confidence == "Low"

    def test_source_name_is_pappers(self):
        from reswip_leads.enrichment.email_sources import PappersEmailSource

        html = '<a href="mailto:x@acme.be">x</a>'
        resp = MagicMock(status_code=200, text=html)
        session = MagicMock()
        session.get.return_value = resp

        src = PappersEmailSource()
        src._session = session
        result = src.find_email("0123.456.789", company_name="Acme SA")

        assert result is not None
        assert result.source == "pappers"


# ── EmailRecheckEnricher ──────────────────────────────────────────


class TestEmailRecheckEnricher:
    def test_priority_chain(self):
        source1 = MagicMock(spec=BaseEmailSource)
        source1.find_email.return_value = EmailCandidate(
            email="first@kbo.be", source="kbo_zip", confidence="High"
        )
        source2 = MagicMock(spec=BaseEmailSource)
        source2.find_email.return_value = EmailCandidate(
            email="second@pappers.be", source="pappers", confidence="Low"
        )

        enricher = EmailRecheckEnricher(sources=[source1, source2])
        result = enricher.enrich(tva="BE0123456789")

        assert result["email"] == "first@kbo.be"
        source1.find_email.assert_called_once()
        source2.find_email.assert_not_called()

    def test_stops_at_first_found(self):
        source1 = MagicMock(spec=BaseEmailSource)
        source1.find_email.return_value = None
        source2 = MagicMock(spec=BaseEmailSource)
        source2.find_email.return_value = EmailCandidate(
            email="found@pappers.be", source="pappers", confidence="Low"
        )
        source3 = MagicMock(spec=BaseEmailSource)
        source3.find_email.return_value = EmailCandidate(
            email="also@website.be", source="website", confidence="Low"
        )

        enricher = EmailRecheckEnricher(sources=[source1, source2, source3])
        result = enricher.enrich(tva="BE0123456789")

        assert result["email"] == "found@pappers.be"
        source1.find_email.assert_called_once()
        source2.find_email.assert_called_once()
        source3.find_email.assert_not_called()

    def test_returns_no_match_when_all_empty(self):
        source1 = MagicMock(spec=BaseEmailSource)
        source1.find_email.return_value = None
        source2 = MagicMock(spec=BaseEmailSource)
        source2.find_email.return_value = None

        enricher = EmailRecheckEnricher(sources=[source1, source2])
        result = enricher.enrich(tva="BE0123456789")

        assert result["status"] == "no_match"
        assert "email" not in result

    def test_returns_error_on_empty_tva(self):
        enricher = EmailRecheckEnricher(sources=[])
        result = enricher.enrich(tva="")

        assert result["status"] == "error"

    def test_set_lead_context(self):
        class FakeLead:
            website = "https://acme.be"

        source = MagicMock(spec=BaseEmailSource)
        source.find_email.return_value = None

        enricher = EmailRecheckEnricher(sources=[source])
        enricher.set_lead_context(FakeLead())
        enricher.enrich(tva="BE0123456789")

        call_kwargs = source.find_email.call_args[1]
        assert call_kwargs["website_url"] == "https://acme.be"

    def test_handles_source_exception(self):
        source1 = MagicMock(spec=BaseEmailSource)
        source1.find_email.side_effect = ConnectionError("network down")
        source2 = MagicMock(spec=BaseEmailSource)
        source2.find_email.return_value = EmailCandidate(
            email="fallback@pappers.be", source="pappers", confidence="Low"
        )

        enricher = EmailRecheckEnricher(sources=[source1, source2])
        result = enricher.enrich(tva="BE0123456789")

        assert result["email"] == "fallback@pappers.be"

    def test_never_overwrites_existing_email(self):
        source = MagicMock(spec=BaseEmailSource)
        source.find_email.return_value = EmailCandidate(
            email="new@acme.be", source="kbo", confidence="Medium"
        )

        enricher = EmailRecheckEnricher(sources=[source])
        result = enricher.enrich(tva="BE0123456789")

        assert result["email"] == "new@acme.be"
        assert result["status"] == "enriched"
        assert result["evidence"][0]["field"] == "email"

    def test_empty_sources_returns_no_match(self):
        enricher = EmailRecheckEnricher(sources=[])
        result = enricher.enrich(tva="BE0123456789")

        assert result["status"] == "no_match"


# ── Standalone CLI / Report ───────────────────────────────────────


class TestStandaloneCLI:
    def test_process_csv_missing_only(self, tmp_path):
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"
        input_csv.write_text(
            "TVA,Company Name,Email\n"
            "BE0123456789,Acme SA,\n"
            "BE9876543210,Other NV,user@example.be\n"
        )

        source = MagicMock(spec=BaseEmailSource)
        source.find_email.return_value = EmailCandidate(
            email="found@acme.be", source="kbo", confidence="Medium"
        )

        stats = _process_csv(
            str(input_csv), str(output_csv), [source], missing_only=True
        )

        assert stats["processed"] == 1
        assert stats["found"] == 1
        assert output_csv.exists()

    def test_process_csv_includes_existing(self, tmp_path):
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"
        input_csv.write_text(
            "TVA,Company Name,Email\n"
            "BE0123456789,Acme SA,user@example.be\n"
        )

        source = MagicMock(spec=BaseEmailSource)
        source.find_email.return_value = None

        stats = _process_csv(
            str(input_csv), str(output_csv), [source], missing_only=False
        )

        assert stats["processed"] == 1

    def test_process_csv_output_has_email_columns(self, tmp_path):
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"
        input_csv.write_text(
            "TVA,Company Name,Email\n"
            "BE0123456789,Acme SA,\n"
        )

        source = MagicMock(spec=BaseEmailSource)
        source.find_email.return_value = EmailCandidate(
            email="info@acme.be", source="kbo", confidence="Medium"
        )

        _process_csv(str(input_csv), str(output_csv), [source], missing_only=True)

        with open(output_csv, "r") as fh:
            reader = csv.DictReader(fh)
            row = next(reader)
            assert row["Email"] == "info@acme.be"
            assert row["Email Source"] == "kbo"
            assert row["Email Confidence"] == "Medium"

    def test_process_csv_empty_input(self, tmp_path):
        input_csv = tmp_path / "input.csv"
        output_csv = tmp_path / "output.csv"
        input_csv.write_text("TVA,Company Name,Email\n")

        stats = _process_csv(
            str(input_csv), str(output_csv), [], missing_only=True
        )

        assert stats["total"] == 0
        assert stats["processed"] == 0

    def test_build_sources_all(self):
        from reswip_leads.enrichment.base import EnrichmentConfig

        config = EnrichmentConfig()
        sources = _build_sources("all", config)
        assert len(sources) == 3

    def test_build_sources_kbo_only(self):
        from reswip_leads.enrichment.base import EnrichmentConfig
        from reswip_leads.enrichment.email_sources import KboEmailSource

        config = EnrichmentConfig()
        sources = _build_sources("kbo", config)
        assert len(sources) == 1
        assert isinstance(sources[0], KboEmailSource)
