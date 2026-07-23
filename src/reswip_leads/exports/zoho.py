"""Zoho CRM CSV and XLSX export.

Exports canonical Lead objects to Zoho-compatible CSV or XLSX files.
Two schemas exist:

- ``ZOHO_COLUMNS``: generic / Insurance (21 columns, comma-delimited)
- ``ENERGY_ZOHO_COLUMNS``: Energy CRM (24 columns, semicolon-delimited,
  UTF-8 with BOM)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from reswip_leads.core.models import Lead
from reswip_leads.core.profile import Profile


# Stable Zoho column order — must not change without migration
ZOHO_COLUMNS = [
    "Business Name",
    "TVA Number",
    "Address",
    "Postal code",
    "City",
    "Province",
    "Region",
    "DB_Region",
    "Language",
    "Phone",
    "Mobile",
    "Email",
    "Website",
    "First Name",
    "Last Name",
    "Position",
    "Contact First Name",
    "Contact Last Name",
    "Category",
    "Organization",
    "Lead Source",
]


def _generic_lead_to_row(lead: Lead, profile: Optional[Profile] = None) -> dict[str, str]:
    """Convert a Lead to a generic Zoho CRM row dictionary."""
    org = ""
    source = ""
    if profile:
        org = profile.extra.get("organization", "")
        source = profile.extra.get("lead_source", "")

    return {
        "Business Name": lead.company_name,
        "TVA Number": lead.tva,
        "Address": lead.address,
        "Postal code": lead.postcode,
        "City": lead.city,
        "Province": lead.province,
        "Region": lead.region,
        "DB_Region": lead.region,
        "Language": lead.language,
        "Phone": lead.phone or "",
        "Mobile": lead.mobile or "",
        "Email": lead.email or "",
        "Website": lead.website or "",
        "First Name": lead.first_name or "",
        "Last Name": lead.last_name or "",
        "Position": lead.position or "",
        "Contact First Name": lead.first_name or "",
        "Contact Last Name": lead.last_name or "",
        "Category": lead.category or "",
        "Organization": org,
        "Lead Source": source,
    }


# ── Energy CRM export ───────────────────────────────────────────

ENERGY_ZOHO_COLUMNS = [
    "Sector of Activity",
    "Business Name",
    "Postal code",
    "City",
    "Region",
    "Province",
    "Address",
    "Phone",
    "Mobile",
    "Fax",
    "webite",
    "Email",
    "TVA Number",
    "First Name",
    "Last Name",
    "Position",
    "Email 1",
    "Contact First Name",
    "Contact Last Name",
    "PreLead Prospect Name",
    "DB_Region",
    "Language",
    "Organization",
    "Lead Source",
]

assert len(ENERGY_ZOHO_COLUMNS) == 24


@dataclass
class EnergyExportMetrics:
    """Counters produced by the Energy CRM export."""

    total_rows: int = 0
    business_name_fallbacks: int = 0


def _energy_prelead_name(lead: Lead) -> str:
    """Build *PreLead Prospect Name*.

    Rules:
    - If ``first_name`` and ``last_name`` exist:
      ``"<first> <LAST>"`` (last name uppercased).
    - Otherwise fall back to ``company_name`` so the field is never empty.
    """
    first = (lead.first_name or "").strip()
    last = (lead.last_name or "").strip()
    if first or last:
        return f"{first} {last.upper()}".strip()
    return lead.company_name


def energy_lead_to_row(
    lead: Lead,
    profile: Optional[Profile] = None,
    metrics: Optional[EnergyExportMetrics] = None,
) -> dict[str, str]:
    """Convert a Lead to an Energy CRM row dictionary."""
    org = ""
    source = ""
    if profile:
        org = profile.extra.get("organization", "")
        source = profile.extra.get("lead_source", "")

    first = (lead.first_name or "").strip()
    last = (lead.last_name or "").strip()
    if first or last:
        prelead = f"{first} {last.upper()}".strip()
    else:
        prelead = lead.company_name
        if metrics is not None:
            metrics.business_name_fallbacks += 1

    db_region = lead.db_region or lead.region

    return {
        "Sector of Activity": lead.category or "",
        "Business Name": lead.company_name,
        "Postal code": lead.postcode,
        "City": lead.city,
        "Region": lead.region,
        "Province": lead.province,
        "Address": lead.address,
        "Phone": lead.phone or "",
        "Mobile": lead.mobile or "",
        "Fax": lead.fax or "",
        "webite": lead.website or "",
        "Email": lead.email or "",
        "TVA Number": lead.tva,
        "First Name": lead.first_name or "",
        "Last Name": (lead.last_name or "").upper(),
        "Position": lead.position or "",
        "Email 1": lead.email or "",
        "Contact First Name": lead.first_name or "",
        "Contact Last Name": (lead.last_name or "").upper(),
        "PreLead Prospect Name": prelead,
        "DB_Region": db_region,
        "Language": lead.language,
        "Organization": org,
        "Lead Source": source,
    }


def export_energy_csv(
    leads: List[Lead],
    output_path: str,
    profile: Optional[Profile] = None,
) -> tuple[str, EnergyExportMetrics]:
    """Export leads to an Energy CRM CSV.

    Formatting rules:
    - Semicolon ``;`` delimiter
    - UTF-8 with BOM
    - ``Last Name`` and ``Contact Last Name`` are uppercased

    Returns ``(output_path, metrics)``.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    metrics = EnergyExportMetrics()
    metrics.total_rows = len(leads)

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=ENERGY_ZOHO_COLUMNS,
            delimiter=";",
            extrasaction="ignore",
        )
        writer.writeheader()
        for lead in leads:
            writer.writerow(energy_lead_to_row(lead, profile, metrics))

    return str(path), metrics


def export_csv(
    leads: List[Lead],
    output_path: str,
    profile: Optional[Profile] = None,
) -> str:
    """Export leads to a Zoho-compatible CSV file.

    Args:
        leads: List of Lead objects to export.
        output_path: Destination file path.
        profile: Optional profile for default values (Organization, Lead Source).

    Returns:
        The output path as a string.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=ZOHO_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            writer.writerow(_generic_lead_to_row(lead, profile))

    return str(path)


def export_xlsx(
    leads: List[Lead],
    output_path: str,
    profile: Optional[Profile] = None,
) -> str:
    """Export leads to a Zoho-compatible XLSX file.

    Requires ``openpyxl`` to be installed.  Falls back to CSV if unavailable.

    Args:
        leads: List of Lead objects to export.
        output_path: Destination file path.
        profile: Optional profile for default values.

    Returns:
        The output path as a string.
    """
    try:
        import openpyxl
    except ImportError:
        # Fall back to CSV
        csv_path = str(Path(output_path).with_suffix(".csv"))
        return export_csv(leads, csv_path, profile)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Leads"

    ws.append(ZOHO_COLUMNS)
    for lead in leads:
        row = _generic_lead_to_row(lead, profile)
        ws.append([row[col] for col in ZOHO_COLUMNS])

    wb.save(str(path))
    return str(path)
