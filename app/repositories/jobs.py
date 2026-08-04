from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, JobStage


STAGE_NAMES = ("import", "classify", "verify", "enrich", "dedupe", "export")


def get_job(session: Session, job_id: str) -> Job | None:
    return session.get(Job, job_id)


def list_jobs(session: Session, limit: int = 50) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)))


def initialize_stages(job: Job) -> None:
    job.stages = [JobStage(name=name) for name in STAGE_NAMES]


def mark_running(job: Job) -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)


def mark_terminal(job: Job, status: str, error: str | None = None) -> None:
    job.status = status
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
