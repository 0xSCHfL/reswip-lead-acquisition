#!/usr/bin/env python3
"""
Download the daily KBO Open Data zip export.

Supported modes:
- portal login using RESWIP_KBO_USERNAME / RESWIP_KBO_PASSWORD
- direct download using RESWIP_KBO_ZIP_URL
- templated direct download using RESWIP_KBO_ZIP_URL_TEMPLATE

If no URL or credentials are provided, the script can still be used in dry-run
mode to validate the target filename for a given date.
"""

from __future__ import annotations

import argparse
import re
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "kbo"
DEFAULT_PREFIX = "KboOpenData_0393"
KBO_LOGIN_URL = "https://kbopub.economie.fgov.be/kbo-open-data/login"
KBO_LOGIN_POST_URL = "https://kbopub.economie.fgov.be/kbo-open-data/static/j_spring_security_check"
KBO_FILES_URL = "https://kbopub.economie.fgov.be/kbo-open-data/affiliation/xml/?files"
DEFAULT_CREDENTIALS_FILE = PROJECT_ROOT / ".secrets" / "kbo_credentials"
KBO_FILE_PATTERN = re.compile(r"files/(?P<name>KboOpenData_\d{4}_\d{4}_\d{2}_\d{2}_(?:Full|Update)\.zip)")


def build_filename(target_date: date, prefix: str = DEFAULT_PREFIX) -> str:
    return f"{prefix}_{target_date:%Y_%m_%d}_Full.zip"


def build_url(target_date: date, filename: str, direct_url: str, url_template: str) -> str:
    if direct_url:
        return direct_url
    if url_template:
        return url_template.format(
            date=target_date.isoformat(),
            yyyy=f"{target_date:%Y}",
            mm=f"{target_date:%m}",
            dd=f"{target_date:%d}",
            filename=filename,
        )
    raise ValueError(
        "No KBO download URL configured. Set RESWIP_KBO_ZIP_URL or RESWIP_KBO_ZIP_URL_TEMPLATE, "
        "or pass --url / --url-template."
    )


def _select_latest_kbo_file(html: str) -> Optional[str]:
    candidates = []
    for match in KBO_FILE_PATTERN.finditer(html):
        filename = match.group("name")
        if "_Full.zip" not in filename:
            continue
        parts = filename.split("_")
        try:
            serial = int(parts[1])
            year = int(parts[2])
            month = int(parts[3])
            day = int(parts[4])
        except (IndexError, ValueError):
            continue
        candidates.append(((year, month, day, serial), f"files/{filename}"))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def download_portal_file(username: str, password: str, destination: Path, force: bool = False) -> bool:
    if destination.exists() and not force:
        size_mb = destination.stat().st_size / (1024 * 1024)
        print(f"  [SKIP] {destination.name} already exists ({size_mb:.2f} MB)")
        return True

    print(f"  Logging in as {username}...")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
    resp = session.post(
        KBO_LOGIN_POST_URL,
        data={"j_username": username, "j_password": password},
        timeout=120,
        allow_redirects=True,
    )
    resp.raise_for_status()
    if "Login" in resp.text and "j_username" in resp.text:
        raise RuntimeError("KBO login failed. Check the credentials file on the server.")

    listing = session.get(KBO_FILES_URL, timeout=120)
    listing.raise_for_status()
    file_href = _select_latest_kbo_file(listing.text)
    if not file_href:
        raise RuntimeError("Could not find a downloadable KBO zip in the authenticated file listing.")

    print(f"  Downloading KBO Open Data ZIP from {file_href}...")
    resp = session.get(f"https://kbopub.economie.fgov.be/kbo-open-data/affiliation/xml/{file_href}", stream=True, timeout=120)
    resp.raise_for_status()

    output_file = destination.parent / Path(file_href).name
    tmp_path = output_file.with_suffix(output_file.suffix + ".part")

    try:
        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(tmp_path, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    percent = (downloaded / total_size) * 100
                    print(f"\r  Progress: {percent:.1f}%", end="", flush=True)

        if tmp_path.exists():
            tmp_path.replace(output_file)
        print()
        size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"  [OK] {output_file.name} ({size_mb:.2f} MB)")
        return True
    except Exception as exc:
        print(f"\n  [ERROR] Failed to download {output_file.name}: {exc}")
        if tmp_path.exists():
            tmp_path.unlink()
        if output_file.exists() and force:
            output_file.unlink()
        return False


def download_direct_file(url: str, destination: Path, force: bool = False) -> bool:
    if destination.exists() and not force:
        size_mb = destination.stat().st_size / (1024 * 1024)
        print(f"  [SKIP] {destination.name} already exists ({size_mb:.2f} MB)")
        return True

    print(f"  Downloading {destination.name}...")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

    tmp_path = destination.with_suffix(destination.suffix + ".part")
    try:
        with requests.get(url, headers=headers, stream=True, timeout=120) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        percent = (downloaded / total_size) * 100
                        print(f"\r  Progress: {percent:.1f}%", end="", flush=True)

        if tmp_path.exists():
            tmp_path.replace(destination)
        print()
        size_mb = destination.stat().st_size / (1024 * 1024)
        print(f"  [OK] {destination.name} ({size_mb:.2f} MB)")
        return True
    except Exception as exc:
        print(f"\n  [ERROR] Failed to download {destination.name}: {exc}")
        if tmp_path.exists():
            tmp_path.unlink()
        if destination.exists() and force:
            destination.unlink()
        return False


def parse_target_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_kbo_credentials(path: Optional[str] = None) -> tuple[str, str]:
    candidate = Path(path) if path else Path(os.getenv("RESWIP_KBO_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_FILE))
    if candidate.exists():
        username = ""
        password = ""
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key in {"username", "reswip_kbo_username", "kbo_username"}:
                username = value
            elif key in {"password", "reswip_kbo_password", "kbo_password"}:
                password = value
        if username and password:
            return username, password

    env_username = os.getenv("RESWIP_KBO_USERNAME", "").strip()
    env_password = os.getenv("RESWIP_KBO_PASSWORD", "").strip()
    if env_username and env_password:
        return env_username, env_password
    raise FileNotFoundError(
        "KBO credentials not found. Set RESWIP_KBO_USERNAME/RESWIP_KBO_PASSWORD or create "
        f"{candidate} with username=... and password=...."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the daily KBO Open Data zip export.")
    parser.add_argument("--date", help="Target date for the KBO zip in YYYY-MM-DD format.")
    parser.add_argument("--output-dir", default=str(DATA_DIR), help="Directory where the zip should be saved.")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="Filename prefix used by the KBO zip.")
    parser.add_argument("--credentials-file", default="", help="Path to a file containing KBO username/password.")
    parser.add_argument("--username", default=os.getenv("RESWIP_KBO_USERNAME", ""), help="KBO portal username.")
    parser.add_argument("--password", default=os.getenv("RESWIP_KBO_PASSWORD", ""), help="KBO portal password.")
    parser.add_argument("--url", default=os.getenv("RESWIP_KBO_ZIP_URL", ""), help="Direct KBO download URL.")
    parser.add_argument(
        "--url-template",
        default=os.getenv("RESWIP_KBO_ZIP_URL_TEMPLATE", ""),
        help="KBO download URL template with {date}, {yyyy}, {mm}, {dd}, and {filename}.",
    )
    parser.add_argument("--force", action="store_true", help="Re-download even if today's file exists.")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved filename and URL without downloading.")
    args = parser.parse_args()

    target_date = parse_target_date(args.date)
    filename = build_filename(target_date, prefix=args.prefix)
    destination = Path(args.output_dir) / filename

    if args.username and args.password:
        username, password = args.username, args.password
    else:
        try:
            username, password = load_kbo_credentials(args.credentials_file or None)
        except FileNotFoundError:
            username = password = ""

    has_portal_auth = bool(username and password)
    has_direct_source = bool(args.url or args.url_template)
    if not has_portal_auth and not has_direct_source and not args.dry_run:
        raise SystemExit(
            "No KBO download source configured. Set RESWIP_KBO_USERNAME/RESWIP_KBO_PASSWORD, "
            "create a credentials file, or set RESWIP_KBO_ZIP_URL / RESWIP_KBO_ZIP_URL_TEMPLATE."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        print(filename)
        if has_portal_auth:
            print(KBO_FILES_URL)
        elif args.url:
            print(args.url)
        elif args.url_template:
            print(build_url(target_date, filename, "", args.url_template))
        return

    print(f"Downloading KBO zip for {target_date.isoformat()}")
    print(f"Target file: {destination}")
    if has_portal_auth:
        print(f"Source URL: {KBO_FILES_URL}")
    else:
        print(f"Source URL: {build_url(target_date, filename, args.url, args.url_template)}")
    print()

    if has_portal_auth:
        ok = download_portal_file(username, password, destination, force=args.force)
    else:
        ok = download_direct_file(build_url(target_date, filename, args.url, args.url_template), destination, force=args.force)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
