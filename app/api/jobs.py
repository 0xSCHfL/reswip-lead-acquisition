from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Job
from app.repositories.jobs import get_job, initialize_stages, list_jobs
from app.schemas import CreateJobRequest, JobDetailResponse, JobEvent, JobResponse
from app.services.files import FileService


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def get_queue(settings: Settings = Depends(get_settings)) -> Queue:
    return Queue("lead-acquisition", connection=Redis.from_url(settings.redis_url))


def _service(settings: Settings) -> FileService:
    return FileService(settings.input_directory, settings.output_directory, settings.upload_directory)


def _response(job: Job) -> JobResponse:
    return JobResponse.model_validate(job)


@router.post("", response_model=JobResponse, status_code=201)
def create_job(
    request: CreateJobRequest,
    session: Session = Depends(get_db),
    queue: Queue = Depends(get_queue),
    settings: Settings = Depends(get_settings),
) -> JobResponse:
    service = _service(settings)
    input_path = Path(request.input_path)
    if not service.validate_input_path(input_path):
        raise HTTPException(status_code=422, detail="input_path must be an existing CSV/XLSX under the configured input directory")
    job = Job(
        workflow=request.workflow,
        input_path=str(input_path.resolve()),
        configuration=request.model_dump(),
    )
    initialize_stages(job)
    session.add(job)
    session.commit()
    session.refresh(job)
    from app.worker import run_pipeline_job

    queue.enqueue(run_pipeline_job, job.id, job.configuration, job_timeout="24h")
    return _response(job)


@router.get("", response_model=list[JobResponse])
def get_jobs(session: Session = Depends(get_db)) -> list[JobResponse]:
    return [_response(job) for job in list_jobs(session)]


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job_detail(job_id: str, session: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> JobDetailResponse:
    job = get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    artifacts = []
    if job.output_directory:
        output_dir = Path(job.output_directory).resolve()
        if output_dir.is_dir():
            for path in sorted(output_dir.iterdir()):
                if path.is_file():
                    artifacts.append({"name": path.name, "path": str(path), "size_bytes": path.stat().st_size})
    payload = _response(job).model_dump() | {
        "total_rows": job.total_rows,
        "names_found": job.names_found,
        "failed_rows": job.failed_rows,
        "review_rows": job.review_rows,
        "error": job.error,
        "stages": job.stages,
        "artifacts": artifacts,
    }
    return JobDetailResponse.model_validate(payload)


@router.get("/{job_id}/events")
async def job_events(job_id: str, session: Session = Depends(get_db)) -> StreamingResponse:
    if get_job(session, job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    def stream() -> Generator[str, None, None]:
        for _ in range(7200):
            job = get_job(session, job_id)
            if job is None:
                break
            event = JobEvent(
                job_id=job.id,
                status=job.status,
                payload={
                    "stages": [
                        {"name": stage.name, "status": stage.status, "completed": stage.completed, "total": stage.total}
                        for stage in job.stages
                    ]
                },
            )
            yield f"data: {json.dumps(event.model_dump(), default=str)}\n\n"
            if job.status in {"completed", "completed_with_warnings", "failed", "cancelled"}:
                break
            session.expire_all()
            # The sync generator yields immediately; the client reconnects or
            # refreshes when the stream closes. A worker update is the source
            # of truth, not the stream itself.

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{job_id}/artifacts/{artifact_name}")
def download_artifact(job_id: str, artifact_name: str, session: Session = Depends(get_db)) -> FileResponse:
    job = get_job(session, job_id)
    if job is None or not job.output_directory:
        raise HTTPException(status_code=404, detail="artifact not found")
    output_dir = Path(job.output_directory).resolve()
    candidate = (output_dir / artifact_name).resolve()
    if candidate.parent != output_dir or not candidate.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(candidate)
