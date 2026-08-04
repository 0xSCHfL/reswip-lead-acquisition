from pathlib import Path

from app.services.pipeline_runner import PipelineJobConfig, PipelineRunner


def test_pipeline_runner_rejects_pipeline_failure(monkeypatch, tmp_path: Path):
    class Failed:
        success = False
        error = "fixture failure"

    monkeypatch.setattr("app.services.pipeline_runner.run_pipeline", lambda **_: Failed())
    config = PipelineJobConfig("input.csv", str(tmp_path), "profiles/energy.yaml")
    try:
        PipelineRunner().run(config)
    except RuntimeError as exc:
        assert str(exc) == "fixture failure"
    else:
        raise AssertionError("expected pipeline failure")
