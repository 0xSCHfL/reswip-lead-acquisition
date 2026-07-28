"""One-command Infobel pipeline: collect links → scrape details.

Usage:
  python pipeline.py "Restaurant" "Liége" -o results.csv
  python pipeline.py "Plombier" "Anvers" -o results.csv --limit 50
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("infobel_pipeline")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect Infobel links and scrape details in one step",
    )
    parser.add_argument("sector", help="Sector/activity to search (e.g. 'Boulanger', 'Restaurant')")
    parser.add_argument("region", help="Region/city to search in (e.g. 'Bruxelles', 'Liége')")
    parser.add_argument("-o", "--output", required=True, help="Output CSV file")
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
