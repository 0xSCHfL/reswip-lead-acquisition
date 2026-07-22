"""Belgian province, region, and language classification."""
from __future__ import annotations


# ── Province → Region mapping ──────────────────────────────────────

_PROVINCE_TO_REGION = {
    # Wallonia
    "Hainaut": "Wallonia",
    "Liège": "Wallonia",
    "Namur": "Wallonia",
    "Luxembourg": "Wallonia",
    "Brabant wallon": "Wallonia",
    "Walloon Brabant": "Wallonia",
    # Brussels
    "Bruxelles": "Brussels",
    "Brussel": "Brussels",
    "Brussels": "Brussels",
    # Flanders
    "Antwerpen": "Flanders",
    "Anvers": "Flanders",
    "Oost-Vlaanderen": "Flanders",
    "East Flanders": "Flanders",
    "West-Vlaanderen": "Flanders",
    "West Flanders": "Flanders",
    "Vlaams-Brabant": "Flanders",
    "Flemish Brabant": "Flanders",
    "Limburg": "Flanders",
}

# ── Province → Language mapping ────────────────────────────────────

_PROVINCE_TO_LANGUAGE = {
    "Hainaut": "FR",
    "Liège": "FR",
    "Namur": "FR",
    "Luxembourg": "FR",
    "Brabant wallon": "FR",
    "Walloon Brabant": "FR",
    "Bruxelles": "FR",
    "Brussel": "NL",
    "Brussels": "FR",
    "Antwerpen": "NL",
    "Anvers": "FR",
    "Oost-Vlaanderen": "NL",
    "East Flanders": "NL",
    "West-Vlaanderen": "NL",
    "West Flanders": "NL",
    "Vlaams-Brabant": "NL",
    "Flemish Brabant": "NL",
    "Limburg": "NL",
}

# ── Canonical province names (lowered key → display name) ─────────

_CANONICAL_PROVINCE = {name.lower(): name for name in _PROVINCE_TO_REGION}


def classify_province(raw: str) -> str:
    """Return the canonical province name, or empty string if unknown."""
    if not raw:
        return ""
    return _CANONICAL_PROVINCE.get(raw.strip().lower(), "")


def classify_region(province: str) -> str:
    """Return Wallonia / Brussels / Flanders for a province name."""
    canonical = classify_province(province)
    if not canonical:
        # Try direct lookup for unknown canonical names
        return _PROVINCE_TO_REGION.get(province.strip().title(), "")
    return _PROVINCE_TO_REGION.get(canonical, "")


def classify_language(province: str) -> str:
    """Return the dominant language code (FR / NL / DE) for a province."""
    canonical = classify_province(province)
    if not canonical:
        return _PROVINCE_TO_LANGUAGE.get(province.strip().title(), "")
    return _PROVINCE_TO_LANGUAGE.get(canonical, "")
