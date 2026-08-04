from __future__ import annotations

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.jobs import get_job, mark_running, mark_terminal
from app.services.files import FileService
from app.services.pipeline_runner import PipelineJobConfig, PipelineRunner


def run_pipeline_job(job_id: str, configuration: dict[str, object]) -> None:
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if job is None:
            return
        mark_running(job)
        session.commit()
        settings = get_settings()
        output_dir = FileService(settings.input_directory, settings.output_directory).output_dir_for(job_id)
        job.output_directory = str(output_dir)
        session.commit()

        def update_stage(stage_name: str, completed: int, total: int) -> None:
            current = next((stage for stage in job.stages if stage.name == stage_name), None)
            if current is None:
                return
            current.status = "completed"
            current.completed = completed
            current.total = total
            session.commit()

        config = PipelineJobConfig(
            input_path=str(job.input_path),
            output_directory=str(output_dir),
            profile_path=str(configuration.get("profile_path", settings.default_profile)),
            enricher=str(configuration.get("enricher", "both")),
            use_kbo=bool(configuration.get("use_kbo", True)),
            use_pappers_fallback=bool(configuration.get("use_pappers_fallback", True)),
            deduplicate=bool(configuration.get("deduplicate", True)),
            output_format=str(configuration.get("output_format", "csv")),
            kbo_zip_path=str(settings.kbo_zip_path) if settings.kbo_zip_path else None,
        )
        summary = PipelineRunner().run(config, progress_callback=update_stage)
        job.total_rows = summary.lead_count
        job.names_found = summary.names_found
        job.failed_rows = summary.failed_rows
        job.review_rows = summary.review_rows
        mark_terminal(job, "completed_with_warnings" if summary.warnings else "completed")
        session.commit()
    except Exception as exc:  # noqa: BLE001
        job = get_job(session, job_id)
        if job is not None:
            mark_terminal(job, "failed", str(exc))
            session.commit()
        raise
    finally:
        session.close()
