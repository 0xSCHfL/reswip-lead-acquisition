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
