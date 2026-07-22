"""KBO Open Data ZIP file reader.

Reads the official KBO bulk export ZIP files for offline verification.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set


def _normalize_code(value: str) -> str:
    return re.sub(r"\D", "", value or "")


@dataclass
class KboRecord:
    """A single enterprise record from the KBO ZIP export."""

    enterprise_number: str
    status: str = ""
    juridical_form: str = ""
    denomination: str = ""
    denomination_language: str = ""
    address: str = ""
    zipcode: str = ""
    municipality: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    activity_codes: Set[str] = field(default_factory=set)
    has_activity: bool = False


class KboZipReader:
    """Read and index KBO Open Data ZIP exports."""

    LANGUAGE_PRIORITY = {"FR": 0, "NL": 1, "DE": 2, "EN": 3}
    ADDRESS_TYPE_PRIORITY = {"REGO": 0, "LEGAL": 1, "MAIN": 2, "HEAD": 3}

    def build_index(
        self,
        zip_path: str,
        targets: Set[str],
        activity_code: str = "",
    ) -> Dict[str, KboRecord]:
        """Build an enterprise-number-indexed dict from a KBO ZIP file.

        Args:
            zip_path: Path to the KBO Open Data ZIP.
            targets: Set of enterprise numbers (digits only) to look up.
            activity_code: Optional NACE code to flag ``has_activity``.

        Returns:
            Dict mapping enterprise_number → :class:`KboRecord`.
        """
        records: Dict[str, KboRecord] = {}
        activity_code = _normalize_code(activity_code)
        found: Set[str] = set()

        with zipfile.ZipFile(zip_path, "r") as zf:
            members = set(zf.namelist())

            if "enterprise.csv" in members:
                for row in self._read_csv(zf, "enterprise.csv"):
                    ent = _normalize_code(row.get("EnterpriseNumber", ""))
                    if ent not in targets:
                        continue
                    record = records.setdefault(ent, KboRecord(ent))
                    found.add(ent)
                    record.status = (row.get("Status") or "").strip()
                    record.juridical_form = (row.get("JuridicalForm") or "").strip()

            if "denomination.csv" in members:
                best: Dict[str, tuple] = {}
                for row in self._read_csv(zf, "denomination.csv"):
                    ent = _normalize_code(row.get("EntityNumber", ""))
                    if ent not in found:
                        continue
                    lang = (row.get("Language") or "").upper()
                    lang_score = self.LANGUAGE_PRIORITY.get(lang, 99)
                    name = (row.get("Denomination") or "").strip()
                    if not name:
                        continue
                    candidate = (lang_score, lang, name)
                    if ent not in best or candidate < best[ent]:
                        best[ent] = candidate
                for ent, (_, lang, name) in best.items():
                    record = records.setdefault(ent, KboRecord(ent))
                    record.denomination = name
                    record.denomination_language = lang

            if "address.csv" in members:
                best_addr: Dict[str, tuple] = {}
                for row in self._read_csv(zf, "address.csv"):
                    ent = _normalize_code(row.get("EntityNumber", ""))
                    if ent not in found:
                        continue
                    addr_type = (row.get("TypeOfAddress") or "").upper()
                    type_score = self.ADDRESS_TYPE_PRIORITY.get(addr_type, 99)
                    zipcode = (row.get("Zipcode") or "").strip()
                    municipality = (row.get("MunicipalityFR") or row.get("MunicipalityNL") or "").strip()
                    street = (row.get("StreetFR") or row.get("StreetNL") or "").strip()
                    house = (row.get("HouseNumber") or "").strip()
                    box = (row.get("Box") or "").strip()
                    full_addr = " ".join(p for p in [street, house, box] if p)
                    candidate = (type_score, 0 if zipcode else 1, zipcode, municipality, full_addr)
                    if ent not in best_addr or candidate < best_addr[ent]:
                        best_addr[ent] = candidate
                for ent, (_, _, zipcode, municipality, full_addr) in best_addr.items():
                    record = records.setdefault(ent, KboRecord(ent))
                    record.zipcode = zipcode
                    record.municipality = municipality
                    record.address = full_addr

            if "contact.csv" in members:
                for row in self._read_csv(zf, "contact.csv"):
                    ent = _normalize_code(row.get("EntityNumber", ""))
                    if ent not in found:
                        continue
                    contact_type = (row.get("ContactType") or "").upper()
                    value = (row.get("Value") or "").strip()
                    if not value:
                        continue
                    record = records.setdefault(ent, KboRecord(ent))
                    if "@" in value and not record.email:
                        record.email = value
                    elif contact_type in {"PHONE", "TEL", "MOBILE"} and not record.phone:
                        record.phone = value
                    elif contact_type in {"WEB", "WEBSITE"} and not record.website:
                        record.website = value

            if "activity.csv" in members:
                for row in self._read_csv(zf, "activity.csv"):
                    ent = _normalize_code(row.get("EntityNumber", ""))
                    if ent not in found:
                        continue
                    code = _normalize_code(row.get("NaceCode", ""))
                    if not code:
                        continue
                    record = records[ent]
                    record.activity_codes.add(code)
                    if activity_code and code == activity_code:
                        record.has_activity = True

        return records

    def _read_csv(self, zf: zipfile.ZipFile, member: str) -> Iterable[Dict[str, str]]:
        with zf.open(member, "r") as handle:
            wrapper = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(wrapper)
            yield from reader
