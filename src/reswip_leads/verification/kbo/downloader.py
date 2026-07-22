"""Generic KBO Open Data ZIP downloader.

This module is sector-neutral: it downloads the official KBO bulk export
without any insurance- or broker-specific logic. The resulting ZIP can be
read by :class:`reswip_leads.verification.kbo.zip_reader.KboZipReader` to
verify companies by TVA.

Operational data (the ZIP itself) is never committed. By default, snapshots
are stored under ``data/kbo/`` inside the working directory, but the output
directory is fully configurable — including a Google Drive mount, e.g.
``/mnt/google-drive/Reswip/data/kbo``.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover - requests is a soft dependency at runtime
    requests = None  # type: ignore


# ── Public configuration constants ─────────────────────────────────

DEFAULT_PREFIX = "KboOpenData_0393"
DEFAULT_CHUNK_SIZE = 1024 * 64
DEFAULT_TIMEOUT = 120
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# Local data directory; can be overridden by --output-dir or
# RESWIP_KBO_OUTPUT_DIR. The .gitignore ships with patterns that exclude
# anything matching data/kbo/*.zip — the ZIP is operational data.
DEFAULT_OUTPUT_DIR = "data/kbo"

# When set, callers may opt into a Google Drive location through configuration
# (e.g. RESWIP_KBO_OUTPUT_DIR=/mnt/google-drive/Reswip/data/kbo). The module
# never auto-commits anything to Git; that is the user's responsibility.
GOOGLE_DRIVE_ENV_HINT = "RESWIP_KBO_OUTPUT_DIR"


# ── Public result types ────────────────────────────────────────────


@dataclass
class DownloadMetadata:
    """Metadata describing a successful KBO ZIP download."""

    filename: str
    size: int
    download_date: str
    source_url: str
    checksum: str = ""
    snapshot_date: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "size": self.size,
            "download_date": self.download_date,
            "source_url": self.source_url,
            "checksum": self.checksum,
            "snapshot_date": self.snapshot_date,
            **({"extra": dict(self.extra)} if self.extra else {}),
        }


class DownloadError(RuntimeError):
    """Raised when a KBO download cannot be completed successfully."""


class InvalidZipError(DownloadError):
    """Raised when the downloaded file is not a valid ZIP archive."""


# ── Filename helpers ───────────────────────────────────────────────


def build_filename(snapshot_date: date, prefix: str = DEFAULT_PREFIX) -> str:
    """Build the canonical KBO Open Data ZIP filename for a given date.

    The default prefix ``KboOpenData_0393`` is the public KBO Open Data
    series identifier. It carries no insurance or broker semantics.
    """
    return f"{prefix}_{snapshot_date:%Y_%m_%d}_Full.zip"


def parse_snapshot_date(value: Optional[Union[str, date]]) -> date:
    """Parse a YYYY-MM-DD string into a :class:`date`; default to today."""
    if value is None or value == "":
        return date.today()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


# ── Core downloader ────────────────────────────────────────────────


class KboDownloader:
    """Download KBO Open Data ZIP snapshots in a sector-neutral way.

    The downloader is intentionally simple and configurable: it takes the
    output directory, the source URL, the snapshot date, and an optional
    filename. It performs the download to a temporary ``.part`` file,
    validates that the result is a real ZIP, computes a SHA-256 checksum,
    and only then atomically renames the file to its final name.
    """

    def __init__(
        self,
        output_dir: Union[str, Path],
        source_url: str,
        snapshot_date: Union[str, date],
        filename: Optional[str] = None,
        prefix: str = DEFAULT_PREFIX,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        timeout: int = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional["requests.Session"] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.source_url = source_url
        self.snapshot_date = parse_snapshot_date(snapshot_date)
        self.filename = filename or build_filename(self.snapshot_date, prefix=prefix)
        self.prefix = prefix
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.user_agent = user_agent
        # session is overridable for tests
        self._session = session

    # ── Public API ────────────────────────────────────────────────

    def download(self) -> DownloadMetadata:
        """Download the configured KBO ZIP and return its metadata."""
        destination = self._prepare_destination()

        with self._open_stream() as response:
            response.raise_for_status()
            tmp_path = self._temp_path_for(destination)
            try:
                size = self._stream_to_temp(response, tmp_path)
                self._validate_zip(tmp_path)
                checksum = self._sha256(tmp_path)
            except Exception:
                # Failed downloads must not leave corrupt partial files.
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

            tmp_path.replace(destination)

        return DownloadMetadata(
            filename=destination.name,
            size=size,
            download_date=datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            source_url=self.source_url,
            checksum=checksum,
            snapshot_date=self.snapshot_date.isoformat(),
        )

    # ── Internals ─────────────────────────────────────────────────

    def _prepare_destination(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir / self.filename

    def _temp_path_for(self, destination: Path) -> Path:
        # Atomic rename: write to .part, rename on success.
        return destination.with_suffix(destination.suffix + ".part")

    def _open_stream(self):
        if requests is None:
            raise DownloadError(
                "The 'requests' library is required for HTTP downloads. "
                "Install it with `pip install requests`."
            )
        session = self._session or requests.Session()
        if self._session is None:
            session.headers.setdefault("User-Agent", self.user_agent)
        return session.get(self.source_url, stream=True, timeout=self.timeout)

    def _stream_to_temp(self, response, tmp_path: Path) -> int:
        total = 0
        with open(tmp_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=self.chunk_size):
                if not chunk:
                    continue
                handle.write(chunk)
                total += len(chunk)
        if total == 0:
            raise DownloadError(
                f"Downloaded file {tmp_path.name} is empty (0 bytes)."
            )
        return total

    @staticmethod
    def _validate_zip(path: Path) -> None:
        """Make sure the downloaded file is a real ZIP archive.

        We open the file directly (no extraction) and let zipfile raise if
        the central directory is corrupt or the magic number is wrong.
        """
        try:
            with zipfile.ZipFile(str(path), "r") as zf:
                # testzip() returns the first bad file or None
                bad = zf.testzip()
                if bad is not None:
                    raise InvalidZipError(
                        f"Downloaded file is not a valid ZIP: corrupt entry {bad!r}"
                    )
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise InvalidZipError(
                f"Downloaded file is not a valid ZIP archive: {exc}"
            ) from exc

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 64), b""):
                digest.update(chunk)
        return digest.hexdigest()


# ── Convenience wrapper ────────────────────────────────────────────


def download_latest(
    output_dir: Union[str, Path],
    source_url: str,
    snapshot_date: Union[str, date, None] = None,
    filename: Optional[str] = None,
    **kwargs,
) -> DownloadMetadata:
    """Download the latest KBO ZIP and return its metadata.

    Thin convenience wrapper around :class:`KboDownloader` for the
    "give me the file" use case.
    """
    downloader = KboDownloader(
        output_dir=output_dir,
        source_url=source_url,
        snapshot_date=snapshot_date or date.today(),
        filename=filename,
        **kwargs,
    )
    return downloader.download()


# ── CLI ────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reswip_leads.verification.kbo.downloader",
        description=(
            "Download the latest KBO Open Data ZIP export. "
            "The file is stored under the output directory and never "
            "automatically committed to Git."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get(GOOGLE_DRIVE_ENV_HINT, DEFAULT_OUTPUT_DIR),
        help=(
            "Directory where the ZIP will be stored. "
            "Defaults to data/kbo, or $RESWIP_KBO_OUTPUT_DIR if set "
            "(e.g. /mnt/google-drive/Reswip/data/kbo)."
        ),
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("RESWIP_KBO_ZIP_URL", ""),
        help="Direct KBO download URL (overrides RESWIP_KBO_ZIP_URL).",
    )
    parser.add_argument(
        "--url-template",
        default=os.environ.get("RESWIP_KBO_ZIP_URL_TEMPLATE", ""),
        help=(
            "URL template with {date}, {yyyy}, {mm}, {dd}, and {filename} "
            "placeholders. Overrides RESWIP_KBO_ZIP_URL_TEMPLATE."
        ),
    )
    parser.add_argument(
        "--date",
        default=os.environ.get("RESWIP_KBO_SNAPSHOT_DATE", ""),
        help="Snapshot date in YYYY-MM-DD. Defaults to today.",
    )
    parser.add_argument(
        "--filename",
        default=os.environ.get("RESWIP_KBO_FILENAME", ""),
        help="Optional explicit filename (overrides the date-based default).",
    )
    parser.add_argument(
        "--prefix",
        default=os.environ.get("RESWIP_KBO_PREFIX", DEFAULT_PREFIX),
        help="KBO filename prefix used for the date-based default name.",
    )
    return parser


def _resolve_url(args: argparse.Namespace, snapshot_date: date, filename: str) -> str:
    if args.url:
        return args.url
    if args.url_template:
        return args.url_template.format(
            date=snapshot_date.isoformat(),
            yyyy=f"{snapshot_date:%Y}",
            mm=f"{snapshot_date:%m}",
            dd=f"{snapshot_date:%d}",
            filename=filename,
        )
    raise SystemExit(
        "No KBO download URL configured. Pass --url or --url-template, "
        "or set RESWIP_KBO_ZIP_URL / RESWIP_KBO_ZIP_URL_TEMPLATE."
    )


def main(argv: Optional[list] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    snapshot_date = parse_snapshot_date(args.date or None)
    filename = args.filename or build_filename(snapshot_date, prefix=args.prefix)
    source_url = _resolve_url(args, snapshot_date, filename)

    print(f"Downloading KBO zip for {snapshot_date.isoformat()}")
    print(f"Output directory: {args.output_dir}")
    print(f"Target file: {filename}")
    print(f"Source URL: {source_url}")

    metadata = download_latest(
        output_dir=args.output_dir,
        source_url=source_url,
        snapshot_date=snapshot_date,
        filename=filename,
    )
    print(
        f"  [OK] {metadata.filename} "
        f"({metadata.size / (1024 * 1024):.2f} MB, "
        f"sha256={metadata.checksum[:12]}...)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
