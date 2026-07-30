"""One-command Infobel pipeline: collect links → scrape details.

Usage:
  python pipeline.py "Restaurant" "Liége" -o results.csv
  python pipeline.py "Plombier" "Anvers" -o results.csv --limit 50
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("infobel_pipeline")


def _write_checkpoint_rows(path: str | Path, rows: list[dict[str, str]]) -> None:
    """Atomically persist all completed Infobel rows."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["search_tva", "infobel_url"]
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(output_path)


def _load_checkpoint_rows(path: str | Path) -> list[dict[str, str]]:
    """Load completed rows from a prior interrupted run."""
    output_path = Path(path)
    if not output_path.exists():
        return []
    with output_path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_tva_records(input_csv: str | Path) -> list[dict[str, str]]:
    """Read TVAs and optional FSMA identity fields from CSV input."""
    input_path = Path(input_csv)
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,").delimiter
        except csv.Error:
            delimiter = ";"
        reader = csv.DictReader(handle, delimiter=delimiter)
        values: list[dict[str, str]] = []
        for row in reader:
            raw_tva = (
                row.get("tva")
                or row.get("TVA Number")
                or row.get("VAT Number")
                or ""
            )
            digits = re.sub(r"\D", "", raw_tva)
            if digits:
                values.append(
                    {
                        "tva": digits,
                        "company_name": row.get("company_name")
                        or row.get("Company Name")
                        or "",
                        "address": row.get("address") or row.get("Address") or "",
                        "postal_code": row.get("postal_code")
                        or row.get("Postal Code")
                        or "",
                        "city": row.get("city") or row.get("City") or "",
                    }
                )
        return values


def _read_tva_values(input_csv: str | Path) -> list[str]:
    """Read normalized digit-only TVAs from comma or semicolon CSV input."""
    return [record["tva"] for record in _read_tva_records(input_csv)]


def _run_tva_batch(
    input_csv: str,
    output: str,
    *,
    headed: bool,
    limit: int | None,
    profile_dir: str,
) -> int:
    """Search Infobel by each TVA, then scrape the returned detail URLs."""
    from .collect_links import collect_tva_links

    input_path = Path(input_csv)
    if not input_path.exists():
        log.error("TVA input CSV not found: %s", input_path)
        return 1

    records = _read_tva_records(input_path)
    completed_rows = _load_checkpoint_rows(output)
    completed_tvas = {
        re.sub(r"\D", "", row.get("search_tva", ""))
        for row in completed_rows
        if row.get("search_tva")
    }
    if completed_tvas:
        log.info("Resuming: %d completed TVA rows loaded from checkpoint", len(completed_tvas))
        records = [record for record in records if record["tva"] not in completed_tvas]

    if limit:
        records = records[:limit]
    if not records:
        if completed_rows:
            log.info("Checkpoint already contains all requested TVA rows")
            return 0
        log.error("TVA input CSV contains no valid TVA values")
        return 1

    # Keep the legacy one-column API working while passing identity fields
    # through for FSMA-enriched inputs.
    tvas = records
    if not any(record["company_name"] or record["address"] for record in records):
        tvas = [record["tva"] for record in records]

    all_rows = list(completed_rows)

    def save_checkpoint(row: dict[str, str]) -> None:
        all_rows.append(row)
        _write_checkpoint_rows(output, all_rows)

    rows = collect_tva_links(
        tvas,
        headed=headed,
        profile_dir=profile_dir,
        on_row=save_checkpoint,
    )

    rows = all_rows
    if not rows:
        log.error("No Infobel detail URLs found for the TVA batch")
        return 1

    _write_checkpoint_rows(output, rows)

    updated = sum(1 for row in rows if (row.get("business_name") or "").strip())
    log.info("TVA batch complete: %d/%d rows scraped", updated, len(rows))
    return 0 if updated > 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect Infobel links and scrape details in one step",
    )
    parser.add_argument("sector", nargs="?", help="Sector/activity to search")
    parser.add_argument("region", nargs="?", help="Region/city to search in")
    parser.add_argument("-o", "--output", required=True, help="Output CSV file")
    parser.add_argument("--input-csv", help="CSV with a tva column for TVA-based fallback")
    parser.add_argument("--headed", action="store_true", default=True, help="Run in headed mode (default: True)")
    parser.add_argument("--no-headed", action="store_true", help="Run in headless mode")
    parser.add_argument("--limit", type=int, default=None, help="Max URLs to collect")
    parser.add_argument("--profile-dir", default="~/.infobel-scrape-profile", help="Persistent Chromium profile directory")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        force=True,
    )

    headed = not args.no_headed

    if args.input_csv:
        return _run_tva_batch(
            args.input_csv,
            args.output,
            headed=headed,
            limit=args.limit,
            profile_dir=args.profile_dir,
        )

    if not args.sector or not args.region:
        parser.error("sector and region are required unless --input-csv is used")

    # ── Step 1: Collect links ─────────────────────────────────
    from .collect_links import collect_links

    log.info("═══ STEP 1: Collecting links ═══")

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, prefix="infobel_links_") as tmp:
        links_csv = tmp.name

    count = collect_links(
        args.sector,
        args.region,
        links_csv,
        headed=headed,
        limit=args.limit,
        profile_dir=args.profile_dir,
    )

    if count == 0:
        log.error("No links collected — aborting")
        return 1

    log.info("Collected %d links → %s", count, links_csv)

    # ── Step 2: Scrape details ────────────────────────────────
    from .scrape_urls import process_csv

    log.info("═══ STEP 2: Scraping details ═══")

    # Copy links CSV to output path first, then scrape in-place
    import shutil
    shutil.copy2(links_csv, args.output)
    log.info("Copied links to %s", args.output)

    updated = process_csv(
        args.output,
        headed=headed,
        profile_dir=args.profile_dir,
    )

    log.info("═══ DONE: %d businesses scraped → %s ═══", updated, args.output)
    return 0 if updated > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
