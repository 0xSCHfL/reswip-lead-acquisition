"""Standalone Energy CRM CSV export CLI.

Usage::

    PYTHONPATH=src python3 -m reswip_leads.exports.zoho_export \\
        --input input.csv --output energy_crm_ready.csv \\
        --profile profiles/energy.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reswip_leads.core.models import Lead
from reswip_leads.core.profile import load_profile
from reswip_leads.exports.zoho import export_energy_csv
from reswip_leads.importers.iqualif import parse_iqualif_csv


def _load_leads(csv_path: str) -> list[Lead]:
    """Load leads from a CSV file (iQualif format)."""
    return parse_iqualif_csv(csv_path)


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
    leads = _load_leads(args.input)

    output_path, metrics = export_energy_csv(leads, args.output, profile)

    print(f"Exported {metrics.total_rows} rows to {output_path}")
    if metrics.business_name_fallbacks:
        print(
            f"  ({metrics.business_name_fallbacks} rows used Business Name fallback "
            f"for PreLead Prospect Name)"
        )


if __name__ == "__main__":
    main()
