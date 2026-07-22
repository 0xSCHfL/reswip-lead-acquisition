"""Tests for the generic KBO ZIP downloader.

These tests do NOT hit the real KBO portal. They use a fake HTTP response
built from a local in-memory ZIP fixture, so the network is never touched.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import List

import pytest

from reswip_leads.verification.kbo.downloader import (
    DEFAULT_PREFIX,
    DownloadError,
    DownloadMetadata,
    InvalidZipError,
    KboDownloader,
    build_filename,
    download_latest,
    parse_snapshot_date,
)


# ── Fake HTTP response ──────────────────────────────────────────────


class _FakeStreamResponse:
    """Mimics a ``requests.Response`` with ``iter_content`` + ``raise_for_status``.

    Implements the context manager protocol so the downloader's
    ``with self._open_stream() as response:`` block works in tests, matching
    the real ``requests.Response`` behavior (closing the connection on exit).
    """

    def __init__(self, payload: bytes, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-length": str(len(payload))}
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 0) -> List[bytes]:
        # Honor the chunk_size contract by slicing the payload.
        if chunk_size <= 0:
            return [self._payload]
        return [
            self._payload[i : i + chunk_size]
            for i in range(0, len(self._payload), chunk_size)
        ]

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.closed = True


class _FakeSession:
    """Drop-in replacement for ``requests.Session`` that records calls."""

    def __init__(self, payload: bytes, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: List[dict] = []
        self.headers: dict = {}

    def get(self, url, stream=False, timeout=None, **kwargs):
        self.calls.append(
            {"url": url, "stream": stream, "timeout": timeout, **kwargs}
        )
        return _FakeStreamResponse(self.payload, status_code=self.status_code)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sample_zip_bytes() -> bytes:
    """Build a tiny in-memory KBO-style ZIP with one CSV inside."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "enterprise.csv",
            "EnterpriseNumber,Status,JuridicalForm\n0123456789,AC,021\n",
        )
    return buffer.getvalue()


# ── Filename generation ─────────────────────────────────────────────


class TestBuildFilename:
    def test_default_prefix(self):
        assert build_filename(date(2026, 7, 22)) == "KboOpenData_0393_2026_07_22_Full.zip"

    def test_custom_prefix(self):
        assert (
            build_filename(date(2025, 1, 5), prefix="CustomPrefix")
            == "CustomPrefix_2025_01_05_Full.zip"
        )

    def test_date_zero_padded(self):
        # Months and days must be zero-padded so KBO servers accept the URL.
        assert build_filename(date(2025, 1, 1)) == "KboOpenData_0393_2025_01_01_Full.zip"


class TestParseSnapshotDate:
    def test_iso_string(self):
        assert parse_snapshot_date("2025-12-31") == date(2025, 12, 31)

    def test_date_instance(self):
        target = date(2024, 6, 15)
        assert parse_snapshot_date(target) is target

    def test_none_defaults_to_today(self):
        assert parse_snapshot_date(None) == date.today()

    def test_empty_defaults_to_today(self):
        assert parse_snapshot_date("") == date.today()

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            parse_snapshot_date("not-a-date")


# ── Output directory creation ───────────────────────────────────────


class TestOutputDirCreation:
    def test_creates_missing_output_dir(self, tmp_path: Path, sample_zip_bytes: bytes):
        output_dir = tmp_path / "deeply" / "nested" / "kbo"
        assert not output_dir.exists()

        session = _FakeSession(sample_zip_bytes)
        downloader = KboDownloader(
            output_dir=output_dir,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        downloader.download()

        assert output_dir.is_dir()
        assert (output_dir / downloader.filename).is_file()

    def test_accepts_google_drive_style_path(self, tmp_path: Path, sample_zip_bytes: bytes):
        # Mimic a mounted Google Drive location — same code path, no special
        # behavior. We must NOT auto-commit it.
        drive_path = tmp_path / "google-drive" / "Reswip" / "data" / "kbo"
        session = _FakeSession(sample_zip_bytes)
        meta = download_latest(
            output_dir=drive_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date=date(2026, 7, 22),
            session=session,
        )
        assert meta.filename == "KboOpenData_0393_2026_07_22_Full.zip"
        assert (drive_path / meta.filename).is_file()


# ── Successful download ─────────────────────────────────────────────


class TestSuccessfulDownload:
    def test_atomic_file_handling(self, tmp_path: Path, sample_zip_bytes: bytes):
        session = _FakeSession(sample_zip_bytes)
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        metadata = downloader.download()

        destination = tmp_path / metadata.filename
        assert destination.is_file()
        # Atomic rename means the .part file must NOT linger after success.
        part = destination.with_suffix(destination.suffix + ".part")
        assert not part.exists()

    def test_metadata_fields(self, tmp_path: Path, sample_zip_bytes: bytes):
        session = _FakeSession(sample_zip_bytes)
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        metadata = downloader.download()

        assert isinstance(metadata, DownloadMetadata)
        assert metadata.filename == "KboOpenData_0393_2026_07_22_Full.zip"
        assert metadata.size == len(sample_zip_bytes)
        assert metadata.source_url == "http://example.invalid/kbo.zip"
        assert metadata.snapshot_date == "2026-07-22"
        assert metadata.download_date.endswith("Z")
        # SHA-256 is 64 hex chars
        assert len(metadata.checksum) == 64
        assert all(c in "0123456789abcdef" for c in metadata.checksum)

    def test_uses_explicit_filename(self, tmp_path: Path, sample_zip_bytes: bytes):
        session = _FakeSession(sample_zip_bytes)
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            filename="custom_name.zip",
            session=session,
        )
        metadata = downloader.download()
        assert metadata.filename == "custom_name.zip"
        assert (tmp_path / "custom_name.zip").is_file()

    def test_checksum_is_stable(self, tmp_path: Path, sample_zip_bytes: bytes):
        session_a = _FakeSession(sample_zip_bytes)
        meta_a = KboDownloader(
            output_dir=tmp_path / "a",
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session_a,
        ).download()

        session_b = _FakeSession(sample_zip_bytes)
        meta_b = KboDownloader(
            output_dir=tmp_path / "b",
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session_b,
        ).download()

        assert meta_a.checksum == meta_b.checksum

    def test_session_called_with_url(self, tmp_path: Path, sample_zip_bytes: bytes):
        session = _FakeSession(sample_zip_bytes)
        url = "http://example.invalid/some/kbo/file.zip"
        KboDownloader(
            output_dir=tmp_path,
            source_url=url,
            snapshot_date="2026-07-22",
            session=session,
        ).download()
        assert session.calls[0]["url"] == url
        assert session.calls[0]["stream"] is True

    def test_to_dict_round_trip(self, tmp_path: Path, sample_zip_bytes: bytes):
        session = _FakeSession(sample_zip_bytes)
        meta = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        ).download()
        d = meta.to_dict()
        assert d["filename"] == meta.filename
        assert d["size"] == meta.size
        assert d["checksum"] == meta.checksum
        assert d["source_url"] == meta.source_url
        assert d["snapshot_date"] == meta.snapshot_date
        assert d["download_date"] == meta.download_date


# ── Invalid ZIP rejection ───────────────────────────────────────────


class TestInvalidZipRejection:
    def test_rejects_garbage_bytes(self, tmp_path: Path):
        garbage = b"this is definitely not a zip file, just plain text"
        session = _FakeSession(garbage)
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        with pytest.raises(InvalidZipError):
            downloader.download()

    def test_rejects_empty_body(self, tmp_path: Path):
        session = _FakeSession(b"")
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        # Empty payload is a 0-byte download — DownloadError, not InvalidZipError.
        with pytest.raises(DownloadError):
            downloader.download()

    def test_failed_download_leaves_no_partial(self, tmp_path: Path):
        garbage = b"not a zip"
        session = _FakeSession(garbage)
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        with pytest.raises(InvalidZipError):
            downloader.download()

        destination = tmp_path / downloader.filename
        part = destination.with_suffix(destination.suffix + ".part")
        # Neither the partial nor the final file should exist.
        assert not destination.exists()
        assert not part.exists()
        # And the directory itself must still be there (atomic create).
        assert tmp_path.is_dir()

    def test_rejects_zip_with_corrupt_central_directory(self, tmp_path: Path):
        # Build a valid ZIP, then corrupt the central directory by overwriting
        # the tail of the buffer. This is enough to make testzip() fail.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("enterprise.csv", "a,b\n1,2\n")
        payload = bytearray(buffer.getvalue())
        # Truncate last 12 bytes (End-of-central-directory record) to damage
        # the central directory.
        corrupt = bytes(payload[:-12])
        session = _FakeSession(corrupt)
        downloader = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            session=session,
        )
        with pytest.raises((InvalidZipError, zipfile.BadZipFile)):
            downloader.download()
        # No leftover partial.
        assert not (tmp_path / downloader.filename).exists()


# ── Sector neutrality ──────────────────────────────────────────────


class TestSectorNeutrality:
    """The downloader must not hardcode insurance/broker concepts."""

    def test_default_prefix_does_not_carry_insurance_meaning(self):
        # The default is the KBO Open Data series identifier, not a sector.
        assert "insurance" not in DEFAULT_PREFIX.lower()
        assert "broker" not in DEFAULT_PREFIX.lower()

    def test_constructor_accepts_arbitrary_prefix(self, tmp_path: Path, sample_zip_bytes: bytes):
        session = _FakeSession(sample_zip_bytes)
        meta = KboDownloader(
            output_dir=tmp_path,
            source_url="http://example.invalid/kbo.zip",
            snapshot_date="2026-07-22",
            prefix="MySectorPrefix",
            session=session,
        ).download()
        assert meta.filename.startswith("MySectorPrefix_")
