from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.jobs import create_job
from app.config import Settings
from app.schemas import CreateJobRequest


def test_create_job_rejects_input_outside_configured_directory(tmp_path: Path):
    request = CreateJobRequest(workflow="enrich_existing", input_path=str(tmp_path / "outside.csv"))
    with pytest.raises(HTTPException) as error:
        create_job(request, session=None, queue=None, settings=Settings(input_directory=tmp_path / "inputs"))
    assert error.value.status_code == 422
