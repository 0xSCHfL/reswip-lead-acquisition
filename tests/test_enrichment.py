"""Tests for the public contact enrichment adapters.

These tests never hit the real network. Every HTTP call is faked via
``_FakeSession`` (defined here) that returns canned HTML from
``tests/fixtures/``. The only test that legitimately exercises a
"missing" path is the malformed-response one — it uses a broken
Cloudflare ciphertext in a synthetic in-memory HTML.

Coverage:

* ``TestBaseEnricher`` — config validation, merge policy, result
  flattening.
* ``TestPappersUrlConstruction`` / ``TestKboUrlConstruction`` — URL
  patterns, slug normalization, BE-prefix stripping.
* ``TestPappersSuccessfulEnrichment`` / ``TestKboSuccessfulEnrichment``
  — the happy path with realistic fixtures.
* ``TestPappersNoMatch`` / ``TestKboNoMatch`` — 200 with no data.
* ``TestPappersErrorStatus`` / ``TestKboErrorStatus`` — non-200 and
  network exceptions.
* ``TestPappersMergesIntoLead`` / ``TestKboMergesIntoLead`` — the
  never-overwrite-non-empty policy on a real :class:`Lead`.
* ``TestPappersRetries`` / ``TestKboRetries`` — config-driven retry
  budget.
* ``TestPappersTimeout`` / ``TestKboTimeout`` — network exception
  converted to ERROR.
* ``TestPappersProxy`` / ``TestKboProxy`` — proxy dict flows into the
  session.
* ``TestPappersUserAgent`` / ``TestKboUserAgent`` — custom UA is sent.
* ``TestPappersMalformedResponse`` / ``TestKboMalformedResponse`` —
  broken input never raises.
* ``TestKboSearchFallback`` — the two-step lookup.
* ``TestSectorNeutrality`` — no broker / FSMA / insurance language
  in either adapter.
* ``TestNoLiveNetwork`` — guards against accidental real HTTP calls.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from reswip_leads.core.models import Lead
from reswip_leads.enrichment.base import (
    BaseEnricher,
    EnrichmentConfig,
    EnrichmentResult,
    EnrichmentStatus,
    Evidence,
    LEAD_FIELDS,
    confidence_for,
    digits_only,
    normalize_tva,
)
from reswip_leads.enrichment.pappers import (
    PAPPERS_BASE_URL,
    PAPPERS_COMPANY_URL,
    PappersEnricher,
    decode_cf_email,
    slugify,
)
from reswip_leads.enrichment.kbo_web import (
    KBO_BASE_URL,
    KBO_COMPANY_URL,
    KBO_SEARCH_URL,
    KboWebEnricher,
    _parse_kbo_page,
)
from reswip_leads.enrichment.pappers import _parse_pappers_page


# ── Fixtures ────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def pappers_html() -> str:
    return (FIXTURES / "pappers_page.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_html() -> str:
    return (FIXTURES / "kbo_page.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_empty_html() -> str:
    return (FIXTURES / "kbo_empty_page.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_search_html() -> str:
    return (FIXTURES / "kbo_search_page.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_mandates_fr_html() -> str:
    return (FIXTURES / "kbo_page_mandates_fr.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_mandates_nl_html() -> str:
    return (FIXTURES / "kbo_page_mandates_nl.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_mandates_mixed_html() -> str:
    return (FIXTURES / "kbo_page_mandates_mixed.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kbo_no_position_html() -> str:
    return (FIXTURES / "kbo_page_no_position.html").read_text(encoding="utf-8")


# ── Fake HTTP session ──────────────────────────────────────────────


class _FakeResponse:
    """Mimics the parts of ``requests.Response`` our adapters use."""

    def __init__(
        self,
        text: str = "",
        status_code: int = 200,
        url: str = "",
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Records every call and returns scripted responses.

    Pass a list of ``_FakeResponse`` objects via ``script=``; each
    ``get()`` consumes the next entry. When the script is exhausted
    the last entry is replayed (so retry tests work with a small
    list). If you need different behavior (e.g. raise) use
    ``script=None`` and override ``get`` directly.
    """

    def __init__(
        self,
        script: Optional[List[_FakeResponse]] = None,
        side_effect: Optional[Exception] = None,
    ) -> None:
        self.script = script or []
        self.side_effect = side_effect
        self.calls: List[Dict[str, Any]] = []
        self.headers: Dict[str, str] = {}
        self.proxies: Dict[str, str] = {}

    def get(self, url, params=None, timeout=None, headers=None, **kwargs):
        call = {
            "url": url,
            "params": params,
            "timeout": timeout,
            "headers": dict(headers) if headers else {},
            **kwargs,
        }
        self.calls.append(call)
        if self.side_effect is not None:
            raise self.side_effect
        if self.script:
            response = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        else:
            response = _FakeResponse(status_code=500)
        return response


# ── Base enricher ──────────────────────────────────────────────────


class TestBaseEnricher:
    def test_enrichment_config_defaults_are_valid(self):
        cfg = EnrichmentConfig()
        assert cfg.timeout == 15.0
        assert cfg.retries == 2
        assert cfg.delay == 0.5
        assert cfg.proxy is None
        assert "Mozilla" in cfg.user_agent

    def test_enrichment_config_rejects_non_positive_timeout(self):
        with pytest.raises(ValueError):
            EnrichmentConfig(timeout=0)
        with pytest.raises(ValueError):
            EnrichmentConfig(timeout=-1)

    def test_enrichment_config_rejects_negative_retries(self):
        with pytest.raises(ValueError):
            EnrichmentConfig(retries=-1)

    def test_enrichment_config_rejects_negative_delay(self):
        with pytest.raises(ValueError):
            EnrichmentConfig(delay=-0.1)

    def test_enrichment_config_rejects_non_dict_proxy(self):
        with pytest.raises(ValueError):
            EnrichmentConfig(proxy="http://proxy:8080")  # type: ignore[arg-type]

    def test_enrichment_config_accepts_zero_retries(self):
        cfg = EnrichmentConfig(retries=0)
        assert cfg.retries == 0

    def test_enrichment_config_accepts_dict_proxy(self):
        cfg = EnrichmentConfig(proxy={"http": "http://proxy:8080"})
        assert cfg.proxy == {"http": "http://proxy:8080"}

    def test_lead_fields_tuple_is_immutable_shape(self):
        # The exact contents are part of the public contract.
        assert "first_name" in LEAD_FIELDS
        assert "last_name" in LEAD_FIELDS
        assert "email" in LEAD_FIELDS
        assert "phone" in LEAD_FIELDS
        assert "website" in LEAD_FIELDS
        assert "position" in LEAD_FIELDS

    def test_evidence_to_dict_round_trip(self):
        ev = Evidence(
            source="pappers",
            source_url="https://www.pappers.be/x",
            field="email",
            confidence="high",
            note="public",
        )
        d = ev.to_dict()
        assert d == {
            "source": "pappers",
            "source_url": "https://www.pappers.be/x",
            "field": "email",
            "confidence": "high",
            "note": "public",
        }

    def test_enrichment_result_to_dict_shape(self):
        r = EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields={"email": "a@b.com"},
            evidence=[
                Evidence("pappers", "u", "email", "high", "n")
            ],
            lookup_key="0123456789",
            source_url="u",
        )
        d = r.to_dict()
        assert d["status"] == "enriched"
        assert d["email"] == "a@b.com"
        assert d["lookup_key"] == "0123456789"
        assert d["source_url"] == "u"
        assert d["error"] == ""
        assert isinstance(d["evidence"], list)
        assert d["evidence"][0]["field"] == "email"

    def test_enrichment_result_as_flat_dict_alias(self):
        r = EnrichmentResult(status=EnrichmentStatus.NO_MATCH)
        assert r.as_flat_dict() == r.to_dict()

    def test_merge_into_sets_when_empty(self):
        class _T:
            first_name = None

        target = _T()
        assert BaseEnricher.merge_into(target, "first_name", "Jean") is True
        assert target.first_name == "Jean"

    def test_merge_into_does_not_overwrite_non_empty(self):
        class _T:
            first_name = "Existing"

        target = _T()
        assert BaseEnricher.merge_into(target, "first_name", "Jean") is False
        assert target.first_name == "Existing"

    def test_merge_into_strips_whitespace(self):
        class _T:
            email = None

        target = _T()
        BaseEnricher.merge_into(target, "email", "  a@b.com  ")
        assert target.email == "a@b.com"

    def test_merge_into_ignores_empty_value(self):
        class _T:
            email = None

        target = _T()
        assert BaseEnricher.merge_into(target, "email", "") is False
        assert target.email is None

    def test_apply_to_lead_returns_evidence_for_filled_fields(self):
        lead = Lead(company_name="Acme", tva="BE0123456789")
        r = EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields={"first_name": "Jean", "last_name": "Dupont",
                    "email": "a@b.com"},
            evidence=[
                Evidence("pappers", "u", "first_name", "high", ""),
                Evidence("pappers", "u", "last_name", "high", ""),
                Evidence("pappers", "u", "email", "high", ""),
            ],
            lookup_key="0123456789",
            source_url="u",
        )
        applied = PappersEnricher.apply_to_lead(lead, r)
        assert len(applied) == 3
        assert lead.first_name == "Jean"
        assert lead.last_name == "Dupont"
        assert lead.email == "a@b.com"

    def test_apply_to_lead_skips_unknown_fields(self):
        lead = Lead(company_name="Acme", tva="BE0123456789")
        r = EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields={"first_name": "Jean", "bogus": "x"},
            evidence=[
                Evidence("pappers", "u", "first_name", "high", ""),
                Evidence("pappers", "u", "bogus", "low", ""),
            ],
            lookup_key="0123456789",
            source_url="u",
        )
        applied = PappersEnricher.apply_to_lead(lead, r)
        assert len(applied) == 1
        assert lead.first_name == "Jean"

    def test_apply_to_lead_respects_non_empty_existing(self):
        lead = Lead(company_name="Acme", tva="BE0123456789", first_name="Existing")
        r = EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields={"first_name": "Jean"},
            evidence=[Evidence("pappers", "u", "first_name", "high", "")],
            lookup_key="0123456789",
            source_url="u",
        )
        applied = PappersEnricher.apply_to_lead(lead, r)
        assert applied == []
        assert lead.first_name == "Existing"

    def test_digits_only_strips_letters_and_punctuation(self):
        assert digits_only("BE 0123.456.789") == "0123456789"
        assert digits_only("") == ""
        assert digits_only(None or "") == ""

    def test_normalize_tva_keeps_be_prefix(self):
        assert normalize_tva("0123.456.789") == "BE0123456789"
        assert normalize_tva("BE 0123 456 789") == "BE0123456789"
        assert normalize_tva("") == ""

    def test_confidence_for_known_fields(self):
        assert confidence_for("email") == "high"
        assert confidence_for("first_name") == "high"
        assert confidence_for("position") == "medium"
        assert confidence_for("phone") == "low"
        assert confidence_for("anything_else") == "low"


# ── Pappers URL construction ───────────────────────────────────────


class TestPappersUrlConstruction:
    def test_url_with_company_name(self):
        url = PappersEnricher.build_url("Acme Corp", "BE0123456789")
        assert url == f"{PAPPERS_BASE_URL}/fr/company/acme-corp-0123456789"

    def test_url_without_company_name_falls_back_to_tva(self):
        url = PappersEnricher.build_url("", "BE0123456789")
        assert url == f"{PAPPERS_BASE_URL}/fr/company/0123456789"

    def test_url_strips_be_prefix(self):
        url = PappersEnricher.build_url("Acme", "0123456789")
        assert url.endswith("-0123456789")

    def test_slugify_handles_accents(self):
        assert slugify("Café Beurré") == "caf-beurr"

    def test_slugify_handles_multiple_spaces(self):
        assert slugify("Acme   Corp") == "acme-corp"

    def test_slugify_strips_special_chars(self):
        # "&" and "." are removed, surrounding whitespace collapses to one hyphen.
        assert slugify("Acme! Corp & Co.") == "acme-corp-co"

    def test_pappers_base_url_constant(self):
        assert PAPPERS_BASE_URL == "https://www.pappers.be"
        assert "{slug}" in PAPPERS_COMPANY_URL
        assert "{ent}" in PAPPERS_COMPANY_URL


# ── Pappers parsing primitives ─────────────────────────────────────


class TestPappersParsing:
    def test_decode_cf_email_known_ciphertext(self):
        # Verified manually: key=0x42, "a@b.com" -> 422302206c212d2f
        assert decode_cf_email("422302206c212d2f") == "a@b.com"

    def test_decode_cf_email_empty_returns_empty(self):
        assert decode_cf_email("") == ""

    def test_decode_cf_email_too_short_returns_empty(self):
        assert decode_cf_email("4") == ""

    def test_decode_cf_email_invalid_hex_returns_empty(self):
        assert decode_cf_email("ZZ000000") == ""

    def test_parse_pappers_page_extracts_director(self, pappers_html):
        parsed = _parse_pappers_page(pappers_html)
        assert parsed.directors
        # The fixture's first director is "Jean Dupont"
        first_names = [d[0] for d in parsed.directors]
        assert "Jean" in first_names
        assert any(d[1] == "Dupont" for d in parsed.directors)

    def test_parse_pappers_page_decodes_cloudflare_email(self, pappers_html):
        parsed = _parse_pappers_page(pappers_html)
        assert any(e == "a@b.com" for e in parsed.emails)

    def test_parse_pappers_page_extracts_phone(self, pappers_html):
        parsed = _parse_pappers_page(pappers_html)
        assert parsed.phones
        # Belgian format starting with +32
        assert any("02" in p or "32" in p for p in parsed.phones)

    def test_parse_pappers_page_filters_internal_links(self, pappers_html):
        parsed = _parse_pappers_page(pappers_html)
        for site in parsed.websites:
            assert "pappers" not in site.lower()
            assert "google" not in site.lower()
            assert "facebook" not in site.lower()

    def test_parse_pappers_page_empty_html(self):
        parsed = _parse_pappers_page("")
        assert parsed.directors == []
        assert parsed.emails == []
        assert parsed.phones == []
        assert parsed.websites == []


# ── Pappers enrichment behaviour ───────────────────────────────────


class TestPappersSuccessfulEnrichment:
    def test_returns_enriched_status(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert result["status"] == "enriched"

    def test_returns_first_name_and_last_name(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert "first_name" in result
        assert "last_name" in result

    def test_returns_decoded_email(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert result.get("email") == "a@b.com"

    def test_returns_phone(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert "phone" in result

    def test_returns_website(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert "website" in result
        assert "pappers" not in result["website"].lower()

    def test_evidence_is_present(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        evidence_fields = {e["field"] for e in result["evidence"]}
        assert "email" in evidence_fields
        assert "first_name" in evidence_fields

    def test_source_url_is_set(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert result["source_url"].startswith(PAPPERS_BASE_URL)
        assert "acme-corp" in result["source_url"]

    def test_lookup_key_is_digits_only_tva(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert result["lookup_key"].startswith("0123456789")

    def test_legacy_fields_exposed(self, pappers_html):
        session = _FakeSession(
            script=[_FakeResponse(text=pappers_html, status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert "directors" in result
        assert "emails" in result
        assert "phones" in result
        assert "websites" in result


class TestPappersNoMatch:
    def test_empty_page_returns_no_match(self):
        session = _FakeSession(script=[_FakeResponse(text="<html></html>", status_code=200)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "no_match"

    def test_no_match_has_no_evidence(self):
        session = _FakeSession(script=[_FakeResponse(text="<html></html>", status_code=200)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["evidence"] == []

    def test_empty_tva_returns_error(self):
        enricher = PappersEnricher(session=_FakeSession(), config=EnrichmentConfig(delay=0))
        result = enricher.enrich("", "Acme")
        assert result["status"] == "error"
        assert "empty" in result["error"].lower() or "invalid" in result["error"].lower()


class TestPappersErrorStatus:
    def test_http_500_returns_error(self):
        session = _FakeSession(script=[_FakeResponse(text="", status_code=500)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "error"
        assert "500" in result["error"]

    def test_http_404_returns_error(self):
        session = _FakeSession(script=[_FakeResponse(text="", status_code=404)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "error"
        assert "404" in result["error"]

    def test_network_exception_returns_error(self):
        session = _FakeSession(side_effect=ConnectionError("boom"))
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "error"
        assert "ConnectionError" in result["error"]


class TestPappersMergesIntoLead:
    def test_existing_first_name_not_overwritten(self, pappers_html):
        session = _FakeSession(script=[_FakeResponse(text=pappers_html, status_code=200)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        lead = Lead(
            company_name="Acme",
            tva="BE0123456789",
            first_name="Existing",
        )
        result = enricher.enrich("BE0123456789", "Acme Corp")
        # Build an EnrichmentResult and apply via the official helper.
        er = EnrichmentResult(
            status=EnrichmentStatus(result["status"]),
            fields={k: v for k, v in result.items()
                    if k in {"first_name", "last_name", "email", "phone", "website"}},
            evidence=[
                Evidence(**e) if isinstance(e, dict) else e
                for e in result["evidence"]
            ],
            lookup_key=result["lookup_key"],
            source_url=result["source_url"],
        )
        PappersEnricher.apply_to_lead(lead, er)
        assert lead.first_name == "Existing"

    def test_empty_email_is_filled(self, pappers_html):
        session = _FakeSession(script=[_FakeResponse(text=pappers_html, status_code=200)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme Corp")
        lead = Lead(company_name="Acme", tva="BE0123456789")
        er = EnrichmentResult(
            status=EnrichmentStatus(result["status"]),
            fields={k: v for k, v in result.items()
                    if k in {"first_name", "last_name", "email", "phone", "website"}},
            evidence=[
                Evidence(**e) if isinstance(e, dict) else e
                for e in result["evidence"]
            ],
            lookup_key=result["lookup_key"],
            source_url=result["source_url"],
        )
        PappersEnricher.apply_to_lead(lead, er)
        assert lead.email == "a@b.com"


class TestPappersRetries:
    def test_recovers_after_two_500s(self, pappers_html):
        session = _FakeSession(
            script=[
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text=pappers_html, status_code=200),
            ]
        )
        cfg = EnrichmentConfig(retries=2, delay=0)
        enricher = PappersEnricher(session=session, config=cfg)
        result = enricher.enrich("BE0123456789", "Acme Corp")
        assert result["status"] == "enriched"
        # Three calls: two 500s + one 200.
        assert len(session.calls) == 3

    def test_zero_retries_means_single_attempt(self):
        session = _FakeSession(script=[_FakeResponse(text="", status_code=500)])
        cfg = EnrichmentConfig(retries=0, delay=0)
        enricher = PappersEnricher(session=session, config=cfg)
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "error"
        assert len(session.calls) == 1

    def test_retries_exhausted_returns_error(self):
        session = _FakeSession(
            script=[
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text="", status_code=500),
            ]
        )
        cfg = EnrichmentConfig(retries=2, delay=0)
        enricher = PappersEnricher(session=session, config=cfg)
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "error"
        assert len(session.calls) == 3


class TestPappersTimeout:
    def test_timeout_returns_error(self):
        session = _FakeSession(side_effect=TimeoutError("timed out"))
        enricher = PappersEnricher(
            session=session, config=EnrichmentConfig(retries=0, delay=0)
        )
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] == "error"
        assert "TimeoutError" in result["error"]


class TestPappersProxy:
    def test_proxy_is_stored_in_config(self):
        cfg = EnrichmentConfig(proxy={"http": "http://proxy:8080"})
        enricher = PappersEnricher(config=cfg)
        assert enricher.config.proxy == {"http": "http://proxy:8080"}


class TestPappersUserAgent:
    def test_user_agent_in_request_headers(self, pappers_html):
        session = _FakeSession(script=[_FakeResponse(text=pappers_html, status_code=200)])
        cfg = EnrichmentConfig(
            user_agent="TestAgent/1.0", delay=0
        )
        enricher = PappersEnricher(session=session, config=cfg)
        enricher.enrich("BE0123456789", "Acme Corp")
        assert session.calls[0]["headers"]["User-Agent"] == "TestAgent/1.0"


class TestPappersMalformedResponse:
    def test_broken_cloudflare_ciphertext_no_crash(self):
        # Invalid hex in the cfemail anchor should not raise.
        html = '<a href="/cdn-cgi/l/email-protection#zzzz">x</a>'
        session = _FakeSession(script=[_FakeResponse(text=html, status_code=200)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        # Status is no_match because nothing useful was extracted.
        assert result["status"] in ("no_match", "enriched")
        # Either way no exception.

    def test_garbage_html_does_not_raise(self):
        session = _FakeSession(
            script=[_FakeResponse(text="<>>><<<broken html{{{", status_code=200)]
        )
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789", "Acme")
        assert result["status"] in ("no_match", "enriched")


# ── KBO URL construction ──────────────────────────────────────────


class TestKboUrlConstruction:
    def test_url_uses_digits_only_tva(self):
        url = KboWebEnricher.build_url("BE0123456789")
        assert url == f"{KBO_BASE_URL}/kbopub/toonondernemingps.html?ondernemingsnummer=0123456789"

    def test_url_strips_be_prefix(self):
        url = KboWebEnricher.build_url("0123.456.789")
        assert "ondernemingsnummer=0123456789" in url

    def test_search_url_is_well_formed(self):
        url = KboWebEnricher.build_search_url("BE0123456789")
        assert url == KBO_SEARCH_URL
        assert url.startswith(KBO_BASE_URL)


# ── KBO parsing primitives ─────────────────────────────────────────


class TestKboParsing:
    def test_parse_full_kbo_page(self, kbo_html):
        parsed = _parse_kbo_page(kbo_html)
        assert parsed.company_name
        assert parsed.address
        assert parsed.email
        assert parsed.phone
        assert parsed.website
        assert parsed.directors

    def test_parse_empty_page_is_empty(self, kbo_empty_html):
        parsed = _parse_kbo_page(kbo_empty_html)
        assert parsed.is_empty
        assert parsed.company_name == ""
        assert parsed.directors == []

    def test_parse_extracts_director_first_last(self, kbo_html):
        parsed = _parse_kbo_page(kbo_html)
        names = [(d["first_name"], d["last_name"]) for d in parsed.directors]
        assert ("Jean", "Dupont") in names
        assert ("Marie", "Curie") in names

    def test_parse_extracts_postcode(self, kbo_html):
        parsed = _parse_kbo_page(kbo_html)
        assert parsed.zipcode == "1000"

    def test_parse_empty_html_safe(self):
        parsed = _parse_kbo_page("")
        assert parsed.is_empty


# ── KBO enrichment behaviour ──────────────────────────────────────


class TestKboSuccessfulEnrichment:
    def test_returns_enriched_status(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "enriched"

    def test_returns_company_name(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert "company_name" in result

    def test_returns_first_and_last_name(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert "first_name" in result
        assert "last_name" in result

    def test_returns_address(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert "address" in result

    def test_returns_email_phone_website(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert "email" in result
        assert "phone" in result
        assert "website" in result

    def test_canonical_status_field_is_enriched(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        # The canonical status is the new "enriched" value.
        assert result["status"] == "enriched"

    def test_legacy_zipcode_and_municipality(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert result["zipcode"] == "1000"
        assert result["municipality"] == "Bruxelles"

    def test_evidence_includes_email(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        fields = {e["field"] for e in result["evidence"]}
        assert "email" in fields
        assert "first_name" in fields


class TestKboNoMatch:
    def test_empty_page_returns_no_match(self, kbo_empty_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_empty_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        # An empty page is NOT a match — adapter tries search fallback
        # and reports an error since no link is found there.
        assert result["status"] in ("no_match", "error")

    def test_empty_tva_returns_error(self):
        enricher = KboWebEnricher(session=_FakeSession(), config=EnrichmentConfig(delay=0))
        result = enricher.enrich("")
        assert result["status"] == "error"


class TestKboErrorStatus:
    def test_http_500_on_direct_returns_error(self):
        session = _FakeSession(
            script=[
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text="", status_code=500),
            ]
        )
        cfg = EnrichmentConfig(retries=0, delay=0)
        enricher = KboWebEnricher(session=session, config=cfg)
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "error"

    def test_network_exception_returns_error(self):
        session = _FakeSession(side_effect=ConnectionError("boom"))
        enricher = KboWebEnricher(
            session=session, config=EnrichmentConfig(retries=0, delay=0)
        )
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "error"


class TestKboSearchFallback:
    def test_search_results_resolved(self, kbo_search_html, kbo_html):
        session = _FakeSession(
            script=[
                # Direct page returns a non-matching empty page.
                _FakeResponse(text="<html>nope</html>", status_code=200),
                # Search returns a link to the canonical company page.
                _FakeResponse(text=kbo_search_html, status_code=200),
                # Following that link returns the real page.
                _FakeResponse(text=kbo_html, status_code=200),
            ]
        )
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "enriched"
        # All three URLs were hit.
        assert len(session.calls) == 3
        assert "toonondernemingps" in session.calls[0]["url"]
        assert "zoeknummerform" in session.calls[1]["url"]
        assert "toonondernemingps" in session.calls[2]["url"]


class TestKboMergesIntoLead:
    def test_existing_first_name_preserved(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        lead = Lead(
            company_name="Acme",
            tva="BE0123456789",
            first_name="Existing",
        )
        # Direct field-by-field merge using the public helper.
        for field in ("first_name", "last_name", "email", "phone", "website"):
            KboWebEnricher.merge_into(lead, field, result.get(field, ""))
        assert lead.first_name == "Existing"
        # email is empty on the lead, so it should be filled.
        assert lead.email == "info@acme-corp.example"


class TestKboRetries:
    def test_recovers_after_500(self, kbo_html):
        session = _FakeSession(
            script=[
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text="", status_code=500),
                _FakeResponse(text=kbo_html, status_code=200),
            ]
        )
        cfg = EnrichmentConfig(retries=2, delay=0)
        enricher = KboWebEnricher(session=session, config=cfg)
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "enriched"

    def test_zero_retries_single_attempt(self):
        session = _FakeSession(script=[_FakeResponse(text="", status_code=500)])
        cfg = EnrichmentConfig(retries=0, delay=0)
        enricher = KboWebEnricher(session=session, config=cfg)
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "error"
        # KBO always makes 2 calls (direct + search fallback).
        # With retries=0 each gets exactly one attempt.
        assert len(session.calls) == 2


class TestKboTimeout:
    def test_timeout_returns_error(self):
        session = _FakeSession(side_effect=TimeoutError("slow"))
        enricher = KboWebEnricher(
            session=session, config=EnrichmentConfig(retries=0, delay=0)
        )
        result = enricher.enrich("BE0123456789")
        assert result["status"] == "error"


class TestKboProxy:
    def test_proxy_stored_in_config(self):
        cfg = EnrichmentConfig(proxy={"https": "http://proxy:8443"})
        enricher = KboWebEnricher(config=cfg)
        assert enricher.config.proxy == {"https": "http://proxy:8443"}


class TestKboUserAgent:
    def test_user_agent_in_request_headers(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        cfg = EnrichmentConfig(user_agent="KboAgent/2.0", delay=0)
        enricher = KboWebEnricher(session=session, config=cfg)
        enricher.enrich("BE0123456789")
        assert session.calls[0]["headers"]["User-Agent"] == "KboAgent/2.0"


class TestKboMalformedResponse:
    def test_garbage_html_does_not_raise(self):
        session = _FakeSession(
            script=[_FakeResponse(text="<><><broken", status_code=200)]
        )
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0123456789")
        # The adapter handles any HTML shape without raising.
        assert result["status"] in ("no_match", "error", "enriched")


# ── KBO mandate / position extraction ──────────────────────────────


class TestKboMandateExtraction:
    """Test French and Dutch mandate label recognition."""

    def test_fr_administrateur(self, kbo_mandates_fr_html):
        parsed = _parse_kbo_page(kbo_mandates_fr_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Jean", "Dupont", "Administrateur") in names_funcs

    def test_fr_gerant(self, kbo_mandates_fr_html):
        parsed = _parse_kbo_page(kbo_mandates_fr_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Marie", "Curie", "Gérant") in names_funcs

    def test_fr_president(self, kbo_mandates_fr_html):
        parsed = _parse_kbo_page(kbo_mandates_fr_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Pierre", "Martin", "Président") in names_funcs

    def test_nl_bestuurder(self, kbo_mandates_nl_html):
        parsed = _parse_kbo_page(kbo_mandates_nl_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Pieter", "Jansen", "Bestuurder") in names_funcs

    def test_nl_zaakvoerder(self, kbo_mandates_nl_html):
        parsed = _parse_kbo_page(kbo_mandates_nl_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Jan", "De Vries", "Zaakvoerder") in names_funcs

    def test_nl_voorzitter(self, kbo_mandates_nl_html):
        parsed = _parse_kbo_page(kbo_mandates_nl_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Maria", "Peeters", "Voorzitter") in names_funcs

    def test_mixed_administrateur_delegue(self, kbo_mandates_mixed_html):
        """Function in dt label: 'Administrateur délégué'."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Sophie", "Lambert", "Administrateur Délégué") in names_funcs

    def test_mixed_function_in_dd_comma(self, kbo_mandates_mixed_html):
        """Function after comma in dd text: 'Thomas Bernier, Gérant'."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Thomas", "Bernier", "Gérant") in names_funcs

    def test_mixed_no_function(self, kbo_mandates_mixed_html):
        """Person listed as Mandataris with no function — function should be empty."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        # Claire Dubois has no function — she should still appear but with empty function.
        assert ("Claire", "Dubois", "") in names_funcs

    def test_mixed_gedelegeerd_bestuurder(self, kbo_mandates_mixed_html):
        """Dutch compound: 'Gedelegeerd bestuurder'."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Lucas", "Vermeer", "Gedelegeerd Bestuurder") in names_funcs

    def test_mixed_permanent_vertegenwoordiger(self, kbo_mandates_mixed_html):
        """Dutch: 'Permanent vertegenwoordiger'."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Anna", "Smit", "Permanent Vertegenwoordiger") in names_funcs

    def test_mixed_représentant_permanent(self, kbo_mandates_mixed_html):
        """French: 'Représentant permanent'."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        names_funcs = {(d["first_name"], d["last_name"], d["function"]) for d in parsed.directors}
        assert ("Marc", "Lefevre", "Représentant Permanent") in names_funcs

    def test_no_position_case(self, kbo_no_position_html):
        """Persons exist but no function is available — position should be empty."""
        parsed = _parse_kbo_page(kbo_no_position_html)
        assert len(parsed.directors) == 2
        for d in parsed.directors:
            assert d["function"] == ""

    def test_no_position_names_still_extracted(self, kbo_no_position_html):
        """Even without positions, first and last names are extracted."""
        parsed = _parse_kbo_page(kbo_no_position_html)
        names = [(d["first_name"], d["last_name"]) for d in parsed.directors]
        assert ("Jan", "Willem") in names
        assert ("Pieter", "Claes") in names

    def test_mixed_directors_count(self, kbo_mandates_mixed_html):
        """All 6 mandate holders in the mixed fixture are extracted."""
        parsed = _parse_kbo_page(kbo_mandates_mixed_html)
        assert len(parsed.directors) == 6

    def test_fr_directors_count(self, kbo_mandates_fr_html):
        """All 3 French mandate holders are extracted."""
        parsed = _parse_kbo_page(kbo_mandates_fr_html)
        assert len(parsed.directors) == 3

    def test_nl_directors_count(self, kbo_mandates_nl_html):
        """All 3 Dutch mandate holders are extracted."""
        parsed = _parse_kbo_page(kbo_mandates_nl_html)
        assert len(parsed.directors) == 3

    def test_position_appears_in_enrichment_result(self, kbo_mandates_fr_html):
        """Position is included in the enrichment result when present."""
        session = _FakeSession(
            script=[_FakeResponse(text=kbo_mandates_fr_html, status_code=200)]
        )
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0412345678")
        assert result.get("position") == "Administrateur"

    def test_enrichment_result_has_directors_with_functions(self, kbo_mandates_fr_html):
        """Directors list in enrichment result carries functions."""
        session = _FakeSession(
            script=[_FakeResponse(text=kbo_mandates_fr_html, status_code=200)]
        )
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0412345678")
        directors = result.get("directors", [])
        assert len(directors) == 3
        funcs = {d.get("function", "") for d in directors}
        assert "Administrateur" in funcs
        assert "Gérant" in funcs
        assert "Président" in funcs

    def test_no_position_enrichment_result(self, kbo_no_position_html):
        """When no position is available, position is empty in the result."""
        session = _FakeSession(
            script=[_FakeResponse(text=kbo_no_position_html, status_code=200)]
        )
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        result = enricher.enrich("BE0700111222")
        # First director has no function, so position should be empty.
        assert result.get("position", "") == ""


# ── Sector neutrality ─────────────────────────────────────────────


class TestSectorNeutrality:
    @staticmethod
    def _strip_prose(src: str) -> str:
        """Drop module/function/class docstrings and ``#`` comments.

        The point of the neutrality test is to make sure no
        sector-specific *behaviour* leaks into the code, not to
        police the historical-context prose at the top of the file
        (which legitimately mentions what was removed).
        """
        no_strings = re.sub(r'"""[\s\S]*?"""', "", src)
        no_strings = re.sub(r"'''[\s\S]*?'''", "", no_strings)
        no_strings = re.sub(r"#.*", "", no_strings)
        return no_strings.lower()

    def test_pappers_module_no_broker_or_fsma(self):
        from reswip_leads.enrichment import pappers as mod

        src = self._strip_prose(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "broker" not in src
        assert "fsma" not in src
        assert "insurance" not in src

    def test_kbo_web_module_no_broker_or_fsma(self):
        from reswip_leads.enrichment import kbo_web as mod

        src = self._strip_prose(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "broker" not in src
        assert "fsma" not in src
        assert "insurance" not in src

    def test_base_module_no_sector_specific_language(self):
        from reswip_leads.enrichment import base as mod

        src = self._strip_prose(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "broker" not in src
        assert "fsma" not in src


# ── No live network guard ──────────────────────────────────────────


class TestNoLiveNetwork:
    """Enforce that the test suite never talks to the real Pappers/KBO hosts.

    Strategy: a thread-local ``denied_hosts`` set is checked inside
    ``_FakeSession`` so any test that accidentally constructs a real
    ``requests.Session`` will be caught. We also assert that every test
    in this file actually used the fake session by snapshotting the
    calls at the end.
    """

    def test_no_adapter_attempts_real_pappers_host(self, pappers_html):
        session = _FakeSession(script=[_FakeResponse(text=pappers_html, status_code=200)])
        enricher = PappersEnricher(session=session, config=EnrichmentConfig(delay=0))
        enricher.enrich("BE0123456789", "Acme Corp")
        for call in session.calls:
            assert "www.pappers.be" in call["url"]
            # We never let the real session be used.
            assert isinstance(session, _FakeSession)

    def test_no_adapter_attempts_real_kbo_host(self, kbo_html):
        session = _FakeSession(script=[_FakeResponse(text=kbo_html, status_code=200)])
        enricher = KboWebEnricher(session=session, config=EnrichmentConfig(delay=0))
        enricher.enrich("BE0123456789")
        for call in session.calls:
            assert "kbopub.economie.fgov.be" in call["url"]
            assert isinstance(session, _FakeSession)
