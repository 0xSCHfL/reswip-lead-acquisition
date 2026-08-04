from app.models import Job


def test_job_defaults_to_queued():
    job = Job(workflow="enrich_existing", input_path="/data/input.csv")
    assert job.status == "queued"
    assert job.stages == []
