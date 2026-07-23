"""KBO (Kruispuntbank) company verification.

Verifies company identity against the Belgian KBO registers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from reswip_leads.verification.kbo.zip_reader import KboZipReader, _normalize_code


class KboVerifier:
    """Verify a company against the Belgian KBO.

    Uses :class:`KboZipReader` for offline ZIP-based verification.
    The verifier reads the KBO Open Data ZIP once, builds a targeted
    index for the requested TVA(s), and returns structured results.

    This is a sector-neutral verifier.  Insurance-specific NACE
    filtering (e.g. 66220) must be handled by the insurance profile
    or module.
    """

    def __init__(self, zip_path: Optional[str] = None) -> None:
        self._zip_path = zip_path
        self._reader = KboZipReader()

    def verify(self, tva: str, zip_path: Optional[str] = None) -> Dict[str, Any]:
        """Verify a single TVA number against the KBO ZIP.

        Args:
            tva: The TVA number to verify (``BE##########`` or digits).
            zip_path: Override the ZIP path set at construction.

        Returns:
            A dict with ``status`` (``verified``/``inactive``/``not_found``),
            plus denomination, address, contacts, and activity codes when
            the enterprise is found.
        """
        path = zip_path or self._zip_path
        enterprise = _normalize_code(tva)
        if not enterprise:
            return self._not_found(tva)

        if not path:
            return self._not_found(tva)

        try:
            index = self._reader.build_index(path, targets={enterprise})
        except Exception:
            return self._not_found(tva)

        record = index.get(enterprise)
        if record is None:
            return self._not_found(tva)

        status = "verified" if record.status in ("AC", "active", "") else "inactive"
        return {
            "status": status,
            "enterprise_number": enterprise,
            "company_name": record.denomination,
            "address": record.address,
            "municipality": record.municipality,
            "zipcode": record.zipcode,
            "email": record.email,
            "phone": record.phone,
            "website": record.website,
            "activity_codes": sorted(record.activity_codes),
            "is_active": status == "verified",
            "directors": [],
        }

    def verify_batch(self, tva_list: list[str], zip_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Verify multiple TVA numbers in a single ZIP read.

        Builds the index once and returns results keyed by TVA.
        """
        path = zip_path or self._zip_path
        enterprises = {_normalize_code(tva) for tva in tva_list if tva}
        enterprises.discard("")

        if not path or not enterprises:
            return {tva: self._not_found(tva) for tva in tva_list}

        try:
            index = self._reader.build_index(path, targets=enterprises)
        except Exception:
            return {tva: self._not_found(tva) for tva in tva_list}

        results: Dict[str, Dict[str, Any]] = {}
        for tva in tva_list:
            enterprise = _normalize_code(tva)
            record = index.get(enterprise)
            if record is None:
                results[tva] = self._not_found(tva)
            else:
                status = "verified" if record.status in ("AC", "active", "") else "inactive"
                results[tva] = {
                    "status": status,
                    "enterprise_number": enterprise,
                    "company_name": record.denomination,
                    "address": record.address,
                    "municipality": record.municipality,
                    "zipcode": record.zipcode,
                    "email": record.email,
                    "phone": record.phone,
                    "website": record.website,
                    "activity_codes": sorted(record.activity_codes),
                    "is_active": status == "verified",
                    "directors": [],
                }
        return results

    @staticmethod
    def _not_found(tva: str) -> Dict[str, Any]:
        enterprise = _normalize_code(tva)
        return {
            "status": "not_found",
            "enterprise_number": enterprise,
            "company_name": "",
            "address": "",
            "municipality": "",
            "zipcode": "",
            "email": "",
            "phone": "",
            "website": "",
            "activity_codes": [],
            "is_active": False,
            "directors": [],
        }
