"""Run the approved three-row Hainaut enrichment pilot.

The source CSV is read-only. Pilot input, enriched output, and a JSON summary
are written to a separate directory.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence

from reswip_leads.core.models import normalize_tva
from reswip_leads.core.profile import load_profile
from reswip_leads.enrichment.base import EnrichmentConfig
from reswip_leads.enrichment.infobel import InfobelEnricher
from reswip_leads.enrichment.kbo_web import KboWebEnricher
from reswip_leads.enrichment.pappers import PappersEnricher
from reswip_leads.pipeline import LeadPipeline
from reswip_leads.verification.kbo.zip_reader import KboZipReader


DEFAULT_SOURCE = (
    "/home/sohaib/GoogleDrive/gdrive/Databases/Energie/Belgium/Wallonie/"
    "Hainaut/iQUALIF-1000-No-Groups/"
    "hainaut_iqualif_1000_no_groups (Copy).csv"
)
DEFAULT_OUTPUT_DIR = "/tmp/reswip-hainaut-three-row-pilot"


def _read_source(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        return list(reader.fieldnames or []), list(reader)


def select_pilot_rows(
    path: str | Path,
    limit: int = 3,
    *,
    include_invalid_tva: bool = False,
) -> List[Dict[str, str]]:
    """Select source rows, requiring valid TVAs unless full mode is enabled."""
    fieldnames, rows = _read_source(Path(path))
    if "Company Name" not in fieldnames or "TVA Number" not in fieldnames:
        raise ValueError("source must contain Company Name and TVA Number columns")

    selected: List[Dict[str, str]] = []
    for row in rows:
        company = (row.get("Company Name") or "").strip()
        tva = normalize_tva(row.get("TVA Number"))
        if not company or (not tva and not include_invalid_tva):
            continue
        copied = dict(row)
        copied["TVA Number"] = tva
        selected.append(copied)
        if len(selected) == limit:
            break

    if len(selected) != limit and not include_invalid_tva:
        raise ValueError(f"source contains fewer than {limit} valid TVA rows")
    return selected


def _write_pilot_input(path: Path, source_path: Path, rows: Sequence[Dict[str, str]]) -> None:
    fieldnames, _ = _read_source(source_path)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter=";",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def run_pilot(
    source_path: str | Path,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    profile_dir: str = "~/.infobel-profile",
    kbo_zip_path: str | Path | None = None,
    limit: int = 3,
    log_file: str | Path | None = None,
    include_invalid_tva: bool = False,
) -> dict:
    source = Path(source_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    selected = select_pilot_rows(
        source,
        limit=limit,
        include_invalid_tva=include_invalid_tva,
    )

    pilot_input = output_root / "hainaut_three_row_input.csv"
    pilot_output = output_root / "hainaut_three_row_enriched.csv"
    summary_path = output_root / "hainaut_three_row_summary.json"
    status_path = output_root / "status.json"
    log_path = Path(log_file) if log_file else output_root / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )

    stage_status: Dict[str, dict] = {}

    def write_status(
        current_stage: str,
        *,
        completed: int = 0,
        total: int = len(selected),
        message: str = "",
    ) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "current_stage": current_stage,
            "completed_rows": completed,
            "total_rows": total,
            "remaining_rows": max(total - completed, 0),
            "message": message,
            "stages": stage_status,
        }
        temporary = status_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(status_path)

    def on_stage(name: str, input_count: int, output_count: int) -> None:
        stage_status[name] = {
            "status": "completed",
            "input_rows": input_count,
            "output_rows": output_count,
        }
        write_status(
            "completed",
            completed=output_count,
            message=f"stage={name} completed",
        )

    write_status("starting", message="pilot process started")
    _write_pilot_input(pilot_input, source, selected)

    config = EnrichmentConfig()
    kbo_zip = Path(kbo_zip_path) if kbo_zip_path else None
    result = LeadPipeline(
        profile=load_profile("energy"),
        output_path=str(pilot_output),
        input_csvs=[str(pilot_input)],
        kbo_zip_path=str(kbo_zip) if kbo_zip else None,
        kbo_reader=KboZipReader() if kbo_zip else None,
        pappers=PappersEnricher(config=config),
        kbo_web=KboWebEnricher(config=config),
        infobel=InfobelEnricher(
            headed=True,
            profile_dir=profile_dir,
            log_file=log_path,
            checkpoint_path=output_root / "infobel_checkpoint.csv",
        ),
        progress=on_stage,
    ).run()

    summary = {
        "source_path": str(source),
        "pilot_input": str(pilot_input),
        "pilot_output": str(pilot_output),
        "success": result.success,
        "error": result.error,
        "lead_count": len(result.leads),
        "stages": [stage.to_dict() for stage in result.stages],
        "leads": [lead.to_dict() for lead in result.leads],
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_status(
        "finished" if result.success else "failed",
        completed=len(result.leads),
        message=result.error or "pipeline finished",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--profile-dir", default="~/.infobel-profile")
    parser.add_argument("--kbo-zip", default="")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--log-file", default="")
    parser.add_argument(
        "--include-invalid-tva",
        action="store_true",
        help="Keep named rows without a TVA (use for the complete source DB)",
    )
    args = parser.parse_args()
    summary = run_pilot(
        args.source,
        args.output_dir,
        args.profile_dir,
        args.kbo_zip or None,
        args.limit,
        args.log_file or None,
        args.include_invalid_tva,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
