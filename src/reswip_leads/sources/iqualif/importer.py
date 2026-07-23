"""iQualif CSV source importer.

Imports leads from iQualif B2B CSV exports.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from reswip_leads.core.models import Lead, normalize_tva


def _normalize_text(value: str) -> str:
    text = re.sub(r"[\W_]+", " ", (value or "").casefold(), flags=re.UNICODE)
    return " ".join(text.split())


def _pick(row: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ";"


class IQualifImporter:
    """Import leads from iQualif CSV files.

    Supports multiple CSV encodings and delimiters.  The importer is
    sector-neutral — no insurance-specific logic.
    """

    def import_leads(self, csv_paths: List[str]) -> List[Lead]:
        """Read one or more CSV files and return a list of :class:`Lead` objects."""
        leads: List[Lead] = []
        for csv_path in csv_paths:
            leads.extend(self._read_csv(Path(csv_path)))
        return leads

    def build_index(self, csv_dir: str, vat_set: Optional[set] = None) -> Dict[str, Dict[str, str]]:
        """Build a VAT-indexed lookup from iQualif CSVs in a directory.

        Returns a dict mapping normalized VAT → field dict.
        """
        root = Path(csv_dir)
        index: Dict[str, Dict[str, str]] = {}
        target_vats = {normalize_tva(v) for v in (vat_set or set()) if v}

        for csv_file in sorted(root.rglob("*.csv")):
            try:
                for row in self._open_rows(csv_file):
                    row_vat = normalize_tva(
                        _pick(row, "VAT Number", "vat_number", "enterprise_number", "Number")
                    )
                    if not row_vat:
                        continue
                    if vat_set and row_vat not in target_vats:
                        continue
                    if row_vat in index:
                        continue

                    index[row_vat] = {
                        "company_name": _pick(row, "Company Name", "Name", "Business Name"),
                        "province": _pick(row, "Province", "province"),
                        "region": _pick(row, "Region", "region"),
                        "city": _pick(row, "City", "city", "Municipality"),
                        "email": _pick(row, "Email", "Email Address", "Mail"),
                        "phone": _pick(row, "Phone", "Telephone", "Office Phone"),
                        "mobile": _pick(row, "Mobile", "Mobile Phone"),
                        "address": _pick(row, "Address", "Straat"),
                        "postcode": _pick(row, "Postcode", "ZIP", "Zipcode"),
                    }
            except Exception:
                continue
        return index

    # ── Internal helpers ───────────────────────────────────────

    def _read_csv(self, path: Path) -> List[Lead]:
        leads: List[Lead] = []
        for row in self._open_rows(path):
            tva = normalize_tva(
                _pick(row, "VAT Number", "vat_number", "enterprise_number", "Number")
            )
            name = _pick(row, "Company Name", "Name", "Business Name", "Enterprise Name")
            if not name:
                continue
            leads.append(
                Lead(
                    company_name=name,
                    tva=tva,
                    address=_pick(row, "Address", "Straat"),
                    city=_pick(row, "City", "city", "Stad"),
                    postcode=_pick(row, "Postcode", "ZIP", "Zipcode"),
                    province=_pick(row, "Province", "province"),
                    region=_pick(row, "Region", "region"),
                    email=_pick(row, "Email", "Email Address", "Mail"),
                    phone=_pick(row, "Phone", "Telephone", "Office Phone"),
                    mobile=_pick(row, "Mobile", "Mobile Phone"),
                    category=_pick(row, "Category", "category", "Sector", "Industry"),
                    source="iQualif",
                )
            )
        return leads

    def _open_rows(self, path: Path) -> Iterator[Dict[str, str]]:
        for encoding in ("utf-8-sig", "latin-1", "cp1252"):
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    sample = handle.read(4096)
                    handle.seek(0)
                    delimiter = _sniff_delimiter(sample)
                    reader = csv.DictReader(handle, delimiter=delimiter)
                    for row in reader:
                        yield row
                return
            except (UnicodeDecodeError, UnicodeError):
                continue
