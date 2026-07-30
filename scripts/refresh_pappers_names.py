"""Refresh First/Last Name/Position from Pappers, preserving other columns."""
from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path

from reswip_leads.enrichment.base import EnrichmentConfig
from reswip_leads.enrichment.pappers import PappersEnricher


log = logging.getLogger("refresh_pappers_names")


def run(input_path: Path, output_path: Path, log_path: Path, missing_only: bool = False) -> None:
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    enricher = PappersEnricher(config=EnrichmentConfig(delay=2.0))
    company_key = "Business Name" if "Business Name" in fieldnames else "Company Name"
    tva_key = "TVA Number"
    invalid_first_names = {
        "administrateur", "administratrice", "bestuurder", "directeur",
        "directrice", "fondateur", "fondatrice", "gérant", "gérante",
        "manager", "président", "présidente", "zaakvoerder",
    }

    pending = [
        (index, row)
        for index, row in enumerate(rows)
        if not missing_only
        or not all((row.get(key) or '').strip() for key in ('First Name', 'Last Name', 'Position'))
    ]
    for completed, (index, row) in enumerate(pending, 1):
        tva = re.sub(r"\D", "", row.get(tva_key, "") or "")
        if not tva:
            continue
        if (row.get("First Name") or "").strip().casefold() in invalid_first_names:
            row["First Name"] = ""
        result = enricher.enrich(tva, row.get(company_key, "") or "")
        first = (result.get("first_name") or "").strip()
        last = (result.get("last_name") or "").strip()
        if first and last:
            row["First Name"] = first
            row["Last Name"] = last
        position = (result.get("position") or "").strip()
        if position:
            row["Position"] = position
        if completed == 1 or completed % 25 == 0 or completed == len(pending):
            log.info("Pappers names progress: completed=%d/%d", completed, len(pending))

        temporary = output_path.with_suffix(output_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--missing-only", action="store_true")
    args = parser.parse_args()
    run(args.input, args.output, args.log, args.missing_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
