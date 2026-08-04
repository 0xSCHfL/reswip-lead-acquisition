from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reswip_leads.pipeline import LeadPipelineResult, run_pipeline


@dataclass(frozen=True)
class PipelineJobConfig:
    input_path: str
    output_directory: str
    profile_path: str
    enricher: str = "both"
    use_kbo: bool = True
    use_pappers_fallback: bool = True
    deduplicate: bool = True
    output_format: str = "csv"
    kbo_zip_path: str | None = None


@dataclass(frozen=True)
class PipelineRunSummary:
    output_path: str
    lead_count: int
    names_found: int
    failed_rows: int
    review_rows: int
    stages: list[dict[str, object]]
    warnings: list[str]


class PipelineRunner:
    def run(self, config: PipelineJobConfig, progress_callback: Callable[[str, int, int], None] | None = None) -> PipelineRunSummary:
        output_dir = Path(config.output_directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        source_name = Path(config.input_path).stem
        output_path = output_dir / f"{source_name}_enriched.{config.output_format}"

        def progress(stage: str, completed: int, total: int) -> None:
            if progress_callback:
                progress_callback(stage, completed, total)

        result: LeadPipelineResult = run_pipeline(
            profile_name=config.profile_path,
            input_csvs=[config.input_path],
            output_path=str(output_path),
            kbo_zip_path=config.kbo_zip_path if config.use_kbo else None,
            output_format=config.output_format,
            enricher=config.enricher if config.use_pappers_fallback else "kbo-web",
            progress=progress,
        )
        if not result.success:
            raise RuntimeError(result.error or "pipeline failed")
        names_found = sum(1 for lead in result.leads if lead.first_name and lead.last_name)
        failed_rows = sum(len(stage.errors) for stage in result.stages)
        review_rows = sum(1 for lead in result.leads if not (lead.first_name and lead.last_name))
        raw_copy = output_dir / f"{source_name}_raw{Path(config.input_path).suffix.lower()}"
        shutil.copy2(config.input_path, raw_copy)
        return PipelineRunSummary(
            output_path=str(output_path),
            lead_count=len(result.leads),
            names_found=names_found,
            failed_rows=failed_rows,
            review_rows=review_rows,
            stages=[stage.to_dict() for stage in result.stages],
            warnings=[error for stage in result.stages for error in stage.errors],
        )
