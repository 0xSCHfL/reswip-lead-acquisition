"""KBO (Kruispuntbank) company verification.

Verifies company identity against the Belgian KBO registers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class KboVerifier:
    """Verify a company against the Belgian KBO.

    This is a sector-neutral verifier.  Insurance-specific NACE
    filtering (e.g. 66220) must be handled by the insurance profile
    or module.
    """

    def verify(self, tva: str) -> Dict[str, Any]:
        """Verify a TVA number against KBO.

        Returns a dict with at minimum:
        - ``status``: one of ``verified``, ``not_found``, ``inactive``, ``error``
        - ``enterprise_number``: the cleaned enterprise number
        - ``company_name``: official denomination (if found)
        - ``address``: registered address
        - ``municipality``: city
        - ``zipcode``: postal code
        - ``activity_codes``: list of NACE codes
        - ``is_active``: bool
        - ``directors``: list of director dicts with first_name, last_name, function
        """
        # Stub — real implementation will call KBO ZIP or KBO web
        return {
            "status": "not_found",
            "enterprise_number": tva.replace("BE", "") if tva.startswith("BE") else tva,
            "company_name": "",
            "address": "",
            "municipality": "",
            "zipcode": "",
            "activity_codes": [],
            "is_active": False,
            "directors": [],
        }

    def verify_batch(self, tva_list: list[str]) -> Dict[str, Dict[str, Any]]:
        """Verify multiple TVA numbers. Returns dict keyed by TVA."""
        return {tva: self.verify(tva) for tva in tva_list}
