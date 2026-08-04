from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal["queued", "running", "completed", "completed_with_warnings", "failed", "cancelled"]
Workflow = Literal["enrich_existing", "scrape_new"]


class CreateJobRequest(BaseModel):
    workflow: Workflow
    input_path: str = Field(min_length=1)
    profile_path: str = "profiles/energy.yaml"
    enricher: Literal["kbo-web", "pappers", "both"] = "both"
    use_kbo: bool = True
    use_pappers_fallback: bool = True
    deduplicate: bool = True
    output_format: Literal["csv", "xlsx"] = "csv"


class InputFileResponse(BaseModel):
    name: str
    path: str
    size_bytes: int
    modified_at: datetime


class StageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    status: str
    completed: int
    total: int
    error: str | None = None


class ArtifactResponse(BaseModel):
    name: str
    path: str
    size_bytes: int


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow: str
    status: JobStatus
    input_path: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobDetailResponse(JobResponse):
    total_rows: int
    names_found: int
    failed_rows: int
    review_rows: int
    error: str | None = None
    stages: list[StageResponse] = Field(default_factory=list)
    artifacts: list[ArtifactResponse] = Field(default_factory=list)


class JobEvent(BaseModel):
    job_id: str
    status: JobStatus
    stage: str | None = None
    completed: int = 0
    total: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
