"""Zoho CRM CSV and XLSX export.

Exports canonical Lead objects to Zoho-compatible CSV or XLSX files.
"""
from __future__ import annotations

import csv
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


def _lead_to_row(lead: Lead, profile: Optional[Profile] = None) -> dict[str, str]:
    """Convert a Lead to a Zoho CRM row dictionary."""
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
            writer.writerow(_lead_to_row(lead, profile))

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
        row = _lead_to_row(lead, profile)
        ws.append([row[col] for col in ZOHO_COLUMNS])

    wb.save(str(path))
    return str(path)
