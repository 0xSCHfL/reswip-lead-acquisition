"""Tests for the Infobel browser-search scraper."""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ── Mock the playwright package so the lazy import succeeds ──────────
_fake_playwright = ModuleType("playwright")
_fake_sync_api = ModuleType("playwright.sync_api")
_fake_playwright.sync_api = _fake_sync_api
_fake_sync_api.sync_playwright = MagicMock()
sys.modules.setdefault("playwright", _fake_playwright)
sys.modules.setdefault("playwright.sync_api", _fake_sync_api)

from reswip_leads.sources.infobel.scraper import (  # noqa: E402
    InfobelRecord,
    InfobelScraper,
    InfobelSearchError,
    _parse_financial_page_text,
    _extract_financial_link,
    _token_summary,
    _validate_results_url,
)


def test_parse_financial_page_text_extracts_company_fields():
    text = """
    Informations financières
    Nom de l'entreprise Been
    Siège SocialRue du Cheval Blanc 41 4347 Fexhe-le-Haut-Clocher LIEGE
    Date de création06/11/2009
    TVABE0820423228
    Année fiscale31/12/2024
    AdministrateurPierre Bolaers
    Joo-Kyung Stassart
    Classification NacebelGestion d’installations sportives
    Nombre d'employés12 (2024)
    """
    result = _parse_financial_page_text(text)
    assert result == {
        "financial_company_name": "Been",
        "financial_registered_office": "Rue du Cheval Blanc 41 4347 Fexhe-le-Haut-Clocher LIEGE",
        "financial_creation_date": "06/11/2009",
        "financial_tva": "BE0820423228",
        "financial_fiscal_year": "31/12/2024",
        "financial_administrators": "Pierre Bolaers; Joo-Kyung Stassart",
        "financial_nacebel": "Gestion d’installations sportives",
        "financial_employee_count": "12 (2024)",
    }


def test_parse_financial_page_text_stops_at_footer_links():
    result = _parse_financial_page_text(
        "Nom de l'entrepriseVN Rocourt\n"
        "Classification NacebelCommerce de détail de vêtements\n"
        "Autres liens:\nPublications comptes annuels\n"
    )
    assert result["financial_company_name"] == "VN Rocourt"
    assert result["financial_nacebel"] == "Commerce de détail de vêtements"


def test_parse_financial_page_text_does_not_treat_footer_as_tva():
    result = _parse_financial_page_text("TVA SUIVEZ NOUS © 1995 - 2026 Infobel")
    assert result["financial_tva"] == ""


# ── Helpers ─────────────────────────────────────────────────────────


def _make_page(html: str = "", url: str = "https://www.infobel.com/fr/belgium/"):
    """Return a mock Playwright Page with controllable behaviour."""
    page = MagicMock()
    page.url = url
    page.content.return_value = html
    page.locator.return_value.count.return_value = 0
    page.locator.return_value.first = MagicMock()
    page.locator.return_value.last = MagicMock()
    page.locator.return_value.nth.return_value = MagicMock()
    page.get_by_text.return_value = MagicMock(count=MagicMock(return_value=0))
    return page


def _results_page_html() -> str:
    return "<html><body>" + "x" * 600 + "No challenge here</body></html>"


def _challenge_page_html() -> str:
    return '<html><body><div id="challenge-running">Checking your browser</div></body></html>'


RESULTS_URL = (
    "https://www.infobel.com/fr/belgium/Search/BusinessResults"
    "?q=Restaurants&l=Chastre&token=abc123def456"
)
BAD_TOKEN_URL = (
    "https://www.infobel.com/fr/belgium/Search/BusinessResults"
    "?q=Restaurants&l=Chastre"
)


def _build_pw_mock(page: MagicMock):
    """Return a MagicMock that looks like the sync_playwright context manager."""
    context = MagicMock()
    context.pages.__getitem__ = lambda self, i: page
    context.pages.__len__ = lambda self: 1
    pw_inst = MagicMock()
    pw_inst.chromium.launch_persistent_context.return_value = context
    pw_mock = MagicMock()
    pw_mock.__enter__ = MagicMock(return_value=pw_inst)
    pw_mock.__exit__ = MagicMock(return_value=False)
    return pw_mock, context, pw_inst


def _setup_page_evaluate(page: MagicMock, token: str = "fake-token-123",
                         fetch_url: str = RESULTS_URL,
                         fetch_body: str | None = None):
    """Configure page for the button-click flow.
    Sets up the #btn-search-header button and page.wait_for_url."""
    _setup_button_click(page, results_url=fetch_url)


def _setup_button_click(page: MagicMock,
                        results_url: str = RESULTS_URL):
    """Configure page mocks for the button-click flow.
    Merges the #btn-search-header mapping into any existing locator side_effect."""
    prev_side_effect = page.locator.side_effect

    btn = MagicMock()
    btn.count.return_value = 1

    if prev_side_effect is not None:
        def _merged(sel):
            if sel == "#btn-search-header":
                return btn
            return prev_side_effect(sel)
        page.locator.side_effect = _merged
    else:
        page.locator.side_effect = lambda sel: {
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

    def _wait_for_url(pattern, **kw):
        page.url = results_url
    page.wait_for_url.side_effect = _wait_for_url


# ── Token summary (safe logging) ────────────────────────────────────


class TestTokenSummary:
    def test_contains_length(self):
        result = _token_summary("abc123def456ghi789")
        assert "len=18" in result

    def test_contains_prefix(self):
        result = _token_summary("abc123def456ghi789")
        assert "prefix=abc123def456" in result

    def test_contains_sha256(self):
        result = _token_summary("test123")
        assert "sha256=" in result

    def test_never_contains_full_token(self):
        token = "super-secret-token-value-that-should-not-leak"
        result = _token_summary(token)
        assert token not in result


# ── URL validation ──────────────────────────────────────────────────


class TestValidateResultsUrl:
    def test_valid_token_url_accepted(self):
        _validate_results_url(RESULTS_URL)

    def test_missing_token_raises(self):
        with pytest.raises(InfobelSearchError, match="Missing or empty 'token'"):
            _validate_results_url(BAD_TOKEN_URL)

    def test_wrong_host_raises(self):
        with pytest.raises(InfobelSearchError, match="Unexpected host"):
            _validate_results_url(
                "https://evil.com/fr/belgium/Search/BusinessResults?token=abc"
            )

    def test_wrong_path_raises(self):
        with pytest.raises(InfobelSearchError, match="does not match"):
            _validate_results_url(
                "https://www.infobel.com/fr/belgium/Other/Page?token=abc"
            )


# ── Persistent profile directory ────────────────────────────────────


class TestPersistentProfileDir:
    @patch("playwright.sync_api.sync_playwright")
    def test_profile_dir_passed_to_launch(self, mock_pw, tmp_path):
        profile = tmp_path / "my-profile"
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search(
            "Restaurants",
            "Chastre",
            profile_dir=str(profile),
        )

        pw_inst.chromium.launch_persistent_context.assert_called_once()
        _, kwargs = pw_inst.chromium.launch_persistent_context.call_args
        assert kwargs["user_data_dir"] == str(profile)

    @patch("playwright.sync_api.sync_playwright")
    def test_default_profile_expands_home(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")

        _, kwargs = pw_inst.chromium.launch_persistent_context.call_args
        expected = str(Path("~/.infobel-profile").expanduser())
        assert kwargs["user_data_dir"] == expected


# ── Headed mode ─────────────────────────────────────────────────────


class TestHeadedMode:
    @patch("playwright.sync_api.sync_playwright")
    def test_headed_sets_headless_false(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("X", "Y", headed=True)

        _, kwargs = pw_inst.chromium.launch_persistent_context.call_args
        assert kwargs["headless"] is False

    @patch("playwright.sync_api.sync_playwright")
    def test_default_headless_true(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("X", "Y")

        _, kwargs = pw_inst.chromium.launch_persistent_context.call_args
        assert kwargs["headless"] is True


# ── Form filling ────────────────────────────────────────────────────


class TestFormFilling:
    @patch("playwright.sync_api.sync_playwright")
    def test_search_term_typed(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        term_input = MagicMock()
        place_input = MagicMock()
        term_input.count.return_value = 1
        place_input.count.return_value = 1
        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.side_effect = lambda sel: {
            "#search-term-input-header": term_input,
            "#search-location-input-header": place_input,
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock

        def _wait_for_url(pattern, **kw):
            page.url = RESULTS_URL
        page.wait_for_url.side_effect = _wait_for_url

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")

        term_input.last.type.assert_called_with("Restaurants", delay=80)

    @patch("playwright.sync_api.sync_playwright")
    def test_location_typed(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        term_input = MagicMock()
        place_input = MagicMock()
        term_input.count.return_value = 1
        place_input.count.return_value = 1
        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.side_effect = lambda sel: {
            "#search-term-input-header": term_input,
            "#search-location-input-header": place_input,
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock

        def _wait_for_url(pattern, **kw):
            page.url = RESULTS_URL
        page.wait_for_url.side_effect = _wait_for_url

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")

        place_input.last.type.assert_called_with("Chastre", delay=80)


# ── Kendo autocomplete selection ──────────────────────────────────


class TestKendoAutocomplete:
    @patch("playwright.sync_api.sync_playwright")
    def test_picks_kendo_item_when_listbox_visible(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        term_input = MagicMock(count=MagicMock(return_value=1))
        place_input = MagicMock(count=MagicMock(return_value=1))
        kendo_item = MagicMock()
        kendo_item.count.return_value = 1
        kendo_item.first.click.return_value = None
        listbox_item_selector = "#search-term-input-header_listbox .k-item"
        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.side_effect = lambda sel: {
            "#search-term-input-header": term_input,
            "#search-location-input-header": place_input,
            listbox_item_selector: kendo_item,
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock

        def _wait_for_url(pattern, **kw):
            page.url = RESULTS_URL
        page.wait_for_url.side_effect = _wait_for_url

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")

        kendo_item.first.click.assert_called()

    @patch("playwright.sync_api.sync_playwright")
    def test_graceful_when_no_kendo_items(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        term_input = MagicMock(count=MagicMock(return_value=1))
        place_input = MagicMock(count=MagicMock(return_value=1))
        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.side_effect = lambda sel: {
            "#search-term-input-header": term_input,
            "#search-location-input-header": place_input,
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")


# ── Submit via Recherche button click ────────────────────────────────


class TestButtonClick:
    @patch("playwright.sync_api.sync_playwright")
    def test_button_click_navigates_to_results(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        term_input = MagicMock(count=MagicMock(return_value=1))
        place_input = MagicMock(count=MagicMock(return_value=1))
        page.locator.side_effect = lambda sel: {
            "#search-term-input-header": term_input,
            "#search-location-input-header": place_input,
            "#btn-search-header": MagicMock(count=MagicMock(return_value=1)),
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")

        # wait_for_url was called to wait for BusinessResults
        page.wait_for_url.assert_called_once_with(
            "**/BusinessResults**", timeout=30_000,
        )


# ── Valid / invalid results URL ─────────────────────────────────────


class TestResultsUrlHandling:
    @patch("playwright.sync_api.sync_playwright")
    def test_button_click_navigates_to_results(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre")

    @patch("playwright.sync_api.sync_playwright")
    def test_abuse_redirect_raises(self, mock_pw):
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock

        def _wait_for_url(pattern, **kw):
            page.url = "https://www.infobel.com/Landing/Abuse"
        page.wait_for_url.side_effect = _wait_for_url

        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.side_effect = lambda sel: {
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        scraper = InfobelScraper()
        with pytest.raises(InfobelSearchError, match="Landing/Abuse"):
            scraper.scrape_search("Restaurants", "Chastre")


# ── Cloudflare challenge handling ───────────────────────────────────


class TestCloudflareHandling:
    @patch("playwright.sync_api.sync_playwright")
    def test_headless_challenge_raises(self, mock_pw):
        page = _make_page(html=_challenge_page_html())
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock

        scraper = InfobelScraper()
        with pytest.raises(InfobelSearchError, match="headless mode"):
            scraper.scrape_search("Restaurants", "Chastre")

    @patch("reswip_leads.sources.infobel.scraper._wait_for_challenge_to_clear")
    @patch("playwright.sync_api.sync_playwright")
    def test_headed_challenge_waits_for_clear(self, mock_pw, mock_wait):
        page = _make_page(html=_challenge_page_html())
        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock

        def clear_challenge(*a, **kw):
            page.content.return_value = _results_page_html()
        mock_wait.side_effect = clear_challenge

        _setup_button_click(page)

        scraper = InfobelScraper()
        scraper.scrape_search("Restaurants", "Chastre", headed=True)

        mock_wait.assert_called_once()


# ── Limit enforcement ───────────────────────────────────────────────


class TestLimitEnforcement:
    @patch("playwright.sync_api.sync_playwright")
    def test_limit_caps_detail_scrapes(self, mock_pw):
        detail_urls = [
            f"https://www.infobel.com/fr/belgium/businessdetails?id={i}"
            for i in range(30)
        ]
        page = _make_page(html=_results_page_html(), url=RESULTS_URL)
        detail_link = MagicMock()
        detail_link.count.return_value = len(detail_urls)
        detail_link.nth.side_effect = lambda i: MagicMock(
            get_attribute=MagicMock(return_value=detail_urls[i])
        )

        btn = MagicMock()
        btn.count.return_value = 1
        page.locator.side_effect = lambda sel: {
            'a[href*="businessdetails"]': detail_link,
            "#btn-search-header": btn,
        }.get(sel, MagicMock(count=MagicMock(return_value=0)))

        record = InfobelRecord(business_name="Test", scrape_date="2025-01-01")

        pw_mock, context, pw_inst = _build_pw_mock(page)
        mock_pw.return_value = pw_mock
        _setup_button_click(page)

        scraper = InfobelScraper()

        with patch.object(scraper, "_scrape_detail") as mock_scrape:
            mock_scrape.return_value = record
            records = scraper.scrape_search("Restaurants", "Chastre", limit=20)

        assert len(records) <= 20
        assert mock_scrape.call_count <= 20


# ── CSV output with search_results_url and scrape_date ──────────────


class TestCsvOutput:
    def test_csv_includes_search_results_url_and_date(self, tmp_path):
        output = tmp_path / "out.csv"
        records = [
            InfobelRecord(
                business_name="Pizza Place",
                search_results_url=RESULTS_URL,
                scrape_date="2025-06-15",
            )
        ]
        InfobelScraper.write_csv(records, str(output))

        with open(output, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["search_results_url"] == RESULTS_URL
        assert rows[0]["scrape_date"] == "2025-06-15"
        assert rows[0]["business_name"] == "Pizza Place"

    def test_csv_header_contains_all_fields(self, tmp_path):
        output = tmp_path / "out.csv"
        InfobelScraper.write_csv([], str(output))

        with open(output, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames

        assert "search_results_url" in header
        assert "scrape_date" in header
        assert "business_name" in header
        assert "tva" in header
        assert "email" in header
        assert "financial_url" in header


# ── Financial link TVA extraction ────────────────────────────────────


class TestFinancialLinkExtraction:
    def _detail_page(self, financial_href: str = "") -> MagicMock:
        page = MagicMock()
        page.url = "https://www.infobel.com/fr/belgium/businessdetails?id=1"
        if financial_href:
            link = MagicMock()
            link.count.return_value = 1
            link.first.get_attribute.return_value = financial_href
            page.locator.return_value = link
        else:
            page.locator.return_value.count.return_value = 0
            page.get_by_text.return_value = MagicMock(count=MagicMock(return_value=0))
        return page

    def test_tva_from_financial_vat_link(self):
        page = self._detail_page(
            financial_href="/fr/belgium/financial/vat/BE2333380827"
        )
        url, tva = _extract_financial_link(
            page, "https://www.infobel.com/fr/belgium/businessdetails?id=1"
        )
        assert tva == "BE2333380827"
        assert "financial/vat/BE2333380827" in url

    def test_no_financial_link_returns_empty(self):
        page = self._detail_page()
        url, tva = _extract_financial_link(
            page, "https://www.infobel.com/fr/belgium/businessdetails?id=1"
        )
        assert url == ""
        assert tva == ""

    def test_financial_link_with_text_fallback(self):
        page = MagicMock()
        page.url = "https://www.infobel.com/fr/belgium/businessdetails?id=1"
        # No href match
        page.locator.return_value.count.return_value = 0
        # Text fallback
        text_link = MagicMock()
        text_link.count.return_value = 1
        text_link.first.get_attribute.return_value = (
            "/fr/belgium/financial/vat/BE0123456789"
        )
        page.get_by_text.return_value = text_link

        url, tva = _extract_financial_link(
            page, "https://www.infobel.com/fr/belgium/businessdetails?id=1"
        )
        assert tva == "BE0123456789"
        assert "financial/vat/BE0123456789" in url

    def test_financial_link_non_vat_url_returns_empty_tva(self):
        page = self._detail_page(financial_href="/fr/belgium/financial/other")
        url, tva = _extract_financial_link(
            page, "https://www.infobel.com/fr/belgium/businessdetails?id=1"
        )
        assert url == "https://www.infobel.com/fr/belgium/financial/other"
        assert tva == ""
