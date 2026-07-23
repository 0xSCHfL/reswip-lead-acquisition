"""Tests for email recheck sources.

Covers:
* ``TestKboZipEmailSource`` — offline KBO ZIP email extraction.
"""
from __future__ import annotations

from typing import Dict, Optional, Set
from unittest.mock import MagicMock, patch

import pytest

from reswip_leads.enrichment.email_sources import (
    BaseEmailSource,
    EmailCandidate,
    KboZipEmailSource,
    _is_valid_email,
)
from reswip_leads.verification.kbo.zip_reader import KboRecord, KboZipReader


# ── Helpers ─────────────────────────────────────────────────────────


def _make_record(email: str = "", enterprise_number: str = "0123456789") -> KboRecord:
    return KboRecord(enterprise_number=enterprise_number, email=email)


def _make_reader(records: Dict[str, KboRecord]) -> MagicMock:
    reader = MagicMock(spec=KboZipReader)
    reader.build_index.return_value = records
    return reader


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
        mock_pw_playwright = MagicMock()
        mock_pw_playwright.chromium.launch.return_value = mock_pw_browser
        mock_pw_playwright.__enter__ = MagicMock(return_value=mock_pw_playwright)
        mock_pw_playwright.__exit__ = MagicMock(return_value=False)

        with (
            patch("reswip_leads.enrichment.email_sources.requests") as mock_req,
            patch(
                "reswip_leads.enrichment.email_sources._pw_playwright",
                mock_pw_playwright,
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

        # Encode "test@acme.be" using CF email protection: key = hex('t') = 74
        key = 0x74
        raw = "test@acme.be"
        encoded = hex(key)[2:]
        for ch in raw:
            encoded += hex(ord(ch) ^ key)[2:]

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
