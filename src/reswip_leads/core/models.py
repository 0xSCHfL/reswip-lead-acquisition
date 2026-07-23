"""Canonical Lead model and TVA normalization."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional


def normalize_tva(value: Optional[str]) -> str:
    """Normalize a Belgian TVA number into ``BE##########`` format.

    Accepts formats like:
    - ``0123456789``
    - ``BE0123456789``
    - ``012.345.678.9``
    - ``012 345 678 9``
    - ``be0123456789``

    Returns empty string for empty or None input.
    """
    vat = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    if not vat:
        return ""
    if vat.startswith("BE"):
        vat = vat[2:]
    return f"BE{vat}" if vat else ""


@dataclass
class Lead:
    """Sector-neutral Belgian lead record.

    TVA (``tva``) is the primary identity key.  Contact fields
    (``first_name``, ``last_name``, ``position``) are optional and
    must never be invented — only filled from reliable sources.
    """

    company_name: str
    tva: str = ""

    # Address
    address: str = ""
    city: str = ""
    postcode: str = ""
    province: str = ""
    region: str = ""
    language: str = ""

    # Contact (all optional)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    website: Optional[str] = None

    # Metadata
    category: str = ""
    db_region: str = ""
    source: str = ""
    nace_codes: str = ""
    status: str = ""

    def __post_init__(self) -> None:
        if not self.company_name or not self.company_name.strip():
            raise ValueError("company_name is required and must not be blank")
        self.company_name = self.company_name.strip()
        self.tva = normalize_tva(self.tva)

    # ── Serialisation ──────────────────────────────────────────

    # Canonical CSV / dict keys used by the pipeline
    _FIELD_MAP = {
        "company_name": "Company Name",
        "tva": "VAT Number",
        "address": "Address",
        "city": "City",
        "postcode": "Postcode",
        "province": "Province",
        "region": "Region",
        "language": "Language",
        "first_name": "First Name",
        "last_name": "Last Name",
        "position": "Position",
        "email": "Email Address",
        "phone": "Office Phone",
        "mobile": "Mobile Phone",
        "fax": "Fax",
        "website": "Website",
        "category": "Category",
        "source": "Source",
        "nace_codes": "NACE Codes",
        "status": "Status",
    }

    def to_dict(self) -> Dict[str, str]:
        """Export to a flat dictionary with pipeline-standard keys."""
        result: Dict[str, str] = {}
        for attr, csv_key in self._FIELD_MAP.items():
            value = getattr(self, attr, "")
            result[csv_key] = "" if value is None else str(value)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> Lead:
        """Import from a flat dictionary (CSV row or similar).

        Accepts both pipeline keys (``Company Name``) and Python
        attribute names (``company_name``).
        """
        reverse_map = {v: k for k, v in cls._FIELD_MAP.items()}
        kwargs: Dict[str, str] = {}
        for key, value in data.items():
            attr = reverse_map.get(key, key)
            if attr in cls._FIELD_MAP:
                kwargs[attr] = value or ""
        return cls(**kwargs)
