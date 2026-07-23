"""Standalone Energy CRM CSV export CLI.

Supports two input formats:

1. Raw iQualif CSV (``Name``, ``Number``, ``Category``, ``Mail``, etc.)
2. Existing Energy CRM / canonical CSV (``Business Name``, ``TVA Number``,
   ``First Name``, ``Last Name``, etc.)

The input format is auto-detected from the header row.

Usage::

    PYTHONPATH=src python3 -m reswip_leads.exports.zoho_export \\
        --input input.csv --output energy_crm_ready.csv \\
        --profile profiles/energy.yaml
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterator

from reswip_leads.core.models import Lead, normalize_tva
from reswip_leads.core.profile import load_profile
from reswip_leads.exports.zoho import export_energy_csv


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ";"


def _open_rows(path: Path) -> Iterator[dict[str, str]]:
    """Yield rows from a CSV trying multiple encodings."""
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as fh:
                sample = fh.read(4096)
                fh.seek(0)
                delimiter = _sniff_delimiter(sample)
                reader = csv.DictReader(fh, delimiter=delimiter)
                for row in reader:
                    yield row
            return
        except (UnicodeDecodeError, UnicodeError):
            continue


def _pick(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_crm_ready(headers: list[str]) -> bool:
    """Detect whether the CSV is already in CRM-ready format."""
    return "Business Name" in headers and "TVA Number" in headers


def _load_crm_ready(path: Path) -> list[Lead]:
    """Load leads from an existing Energy CRM CSV."""
    leads: list[Lead] = []
    for row in _open_rows(path):
        name = _pick(row, "Business Name", "Company Name", "Name")
        if not name:
            continue
        leads.append(
            Lead(
                company_name=name,
                tva=normalize_tva(_pick(row, "TVA Number", "Number")),
                address=_pick(row, "Address"),
                city=_pick(row, "City"),
                postcode=_pick(row, "Postal code", "Postcode", "ZIP"),
                province=_pick(row, "Province"),
                region=_pick(row, "Region"),
                db_region=_pick(row, "DB_Region"),
                language=_pick(row, "Language"),
                first_name=_pick(row, "First Name", "Contact First Name"),
                last_name=_pick(row, "Last Name", "Contact Last Name"),
                position=_pick(row, "Position"),
                email=_pick(row, "Email"),
                email1=_pick(row, "Email 1"),
                phone=_pick(row, "Phone"),
                mobile=_pick(row, "Mobile"),
                fax=_pick(row, "Fax"),
                website=_pick(row, "webite", "Website"),
                category=_pick(row, "Sector of Activity", "Category"),
            )
        )
    return leads


def _load_iqualif(path: Path) -> list[Lead]:
    """Load leads from a raw iQualif CSV."""
    from reswip_leads.sources.iqualif.importer import IQualifImporter

    imp = IQualifImporter()
    return imp.import_leads([str(path)])


def load_leads(csv_path: str) -> list[Lead]:
    """Load leads, auto-detecting the input format from headers."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {csv_path}")

    # Peek at the header to detect format
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as fh:
                sample = fh.read(4096)
                fh.seek(0)
                delimiter = _sniff_delimiter(sample)
                reader = csv.DictReader(fh, delimiter=delimiter)
                headers = reader.fieldnames or []
                break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        headers = []

    if _is_crm_ready(headers):
        return _load_crm_ready(path)
    return _load_iqualif(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export leads to Energy CRM-ready CSV (24 columns, semicolon-delimited)."
    )
    parser.add_argument("--input", required=True, help="Input CSV file path")
    parser.add_argument("--output", required=True, help="Output CSV file path")
    parser.add_argument(
        "--profile",
        default="energy.yaml",
        help="YAML profile path or name (default: energy.yaml)",
    )
    args = parser.parse_args(argv)

    if not Path(args.input).exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(args.profile)
    leads = load_leads(args.input)

    output_path, metrics = export_energy_csv(leads, args.output, profile)

    print(f"Exported {metrics.total_rows} rows to {output_path}")
    if metrics.business_name_fallbacks:
        print(
            f"  ({metrics.business_name_fallbacks} rows used Business Name fallback "
            f"for PreLead Prospect Name)"
        )


if __name__ == "__main__":
    main()
