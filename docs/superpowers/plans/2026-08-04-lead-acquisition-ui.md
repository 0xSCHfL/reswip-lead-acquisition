# Lead Acquisition UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Docker Compose web MVP that creates background enrichment jobs for iQualif files, reports stage progress, and provides downloadable enriched outputs.

**Architecture:** Add a small FastAPI application under `app/` that owns job configuration, persistence, and artifact downloads. RQ workers execute an adapter around the existing `LeadPipeline`, while PostgreSQL stores job metadata and Redis stores the queue. Add a React/TypeScript frontend under `frontend/` for the dashboard, job wizard, and progress/results pages.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, RQ, Redis, React, TypeScript, Vite, Tailwind CSS, shadcn/ui, Server-Sent Events, Docker Compose.

## Global Constraints

- The original input file is never overwritten; every job writes a separate output artifact.
- TVA/VAT is the primary company matching key.
- KBO is the primary source for company status and mandate-holder names.
- Pappers is a bounded fallback when KBO has no usable person data.
- Existing first name, last name, and position values are preserved.
- Unverified names remain blank; timeouts and blocked pages never become data.
- CSV inputs and outputs remain in the configured Google Drive-backed filesystem; PostgreSQL stores paths and checksums.
- Use RQ + Redis for the first release; do not introduce Celery.
- Preserve unrelated existing worktree changes in `src/reswip_leads/enrichment/pappers.py`, `src/reswip_leads/sources/infobel/recaptcha_solver.py`, `tests/test_enrichment.py`, `scripts/pappers/`, and `tests/test_recaptcha_solver.py`.

## File Map

- Create `app/main.py`: FastAPI application and route registration.
- Create `app/config.py`: environment-backed settings, including the configured Iqualif directory.
- Create `app/db.py`: SQLAlchemy engine, session factory, and database dependency.
- Create `app/models.py`: `Job`, `JobStage`, `JobRecord`, and `Evidence` persistence models.
- Create `app/schemas.py`: request/response models used by the API.
- Create `app/repositories/jobs.py`: job and stage persistence operations.
- Create `app/services/files.py`: safe input listing, output path creation, and artifact validation.
- Create `app/services/pipeline_runner.py`: bridge from API job configuration to `LeadPipeline` and progress callbacks.
- Create `app/worker.py`: RQ job entry point and cancellation-safe state updates.
- Create `app/api/jobs.py`: job creation, listing, detail, progress stream, cancel, and download routes.
- Create `app/api/files.py`: input-file listing route.
- Create `tests/app/`: API, repository, service, and worker tests.
- Create `frontend/`: Vite React application with wizard, job list, job detail, and shared API client.
- Create `docker-compose.yml`, `Dockerfile`, `frontend/Dockerfile`, and `.env.example`.
- Modify `pyproject.toml`: add the backend runtime and test dependencies.
- Modify `README.md`: local startup, Google Drive path configuration, and first-job instructions.

---

### Task 1: Backend foundation and persistence

**Files:**
- Create: `app/config.py`, `app/db.py`, `app/models.py`, `app/schemas.py`, `app/main.py`
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_create_jobs.py`
- Modify: `pyproject.toml`
- Test: `tests/app/test_database_models.py`

**Interfaces:**
- `JobStatus = Literal["queued", "running", "completed", "completed_with_warnings", "failed", "cancelled"]`.
- `Job.workflow: Literal["enrich_existing", "scrape_new"]`.
- `POST /api/jobs` accepts `CreateJobRequest` and returns `JobResponse` with `id`, `status`, `workflow`, `input_path`, and timestamps.
- `GET /api/jobs/{job_id}` returns `JobDetailResponse` including stage counters and output artifacts.

- [ ] **Step 1: Add backend dependencies and package markers.**

Add FastAPI, Uvicorn, SQLAlchemy, Alembic, psycopg, RQ, Redis, and `pydantic-settings` to the project dependencies. Add `app/__init__.py` and `tests/app/__init__.py`.

- [ ] **Step 2: Write model tests first.**

```python
def test_job_defaults_to_queued(session):
    job = Job(workflow="enrich_existing", input_path="/data/input.csv")
    session.add(job)
    session.commit()
    assert job.status == "queued"
    assert job.stages == []
```

- [ ] **Step 3: Implement settings, database session, and models.**

Use `DATABASE_URL`, `REDIS_URL`, `INPUT_DIRECTORY`, and `OUTPUT_DIRECTORY` settings. Define relationships from `Job` to `JobStage`, `JobRecord`, and `Evidence`, with cascade deletion only for job-owned metadata. Do not store CSV contents in PostgreSQL.

- [ ] **Step 4: Add the initial Alembic migration.**

Create tables for jobs, job stages, job records, and evidence with indexes on `jobs.status`, `jobs.created_at`, `job_records.job_id`, and `evidence.job_record_id`.

- [ ] **Step 5: Run the focused test.**

Run: `pytest tests/app/test_database_models.py -q`

Expected: PASS, or an explicit dependency/setup failure that is fixed before continuing.

- [ ] **Step 6: Commit the foundation.**

```bash
git add app tests/app pyproject.toml alembic.ini alembic
git commit -m "feat: add lead job persistence foundation"
```

### Task 2: File handling and job API

**Files:**
- Create: `app/repositories/jobs.py`, `app/services/files.py`, `app/api/jobs.py`, `app/api/files.py`
- Modify: `app/main.py`
- Test: `tests/app/test_file_service.py`, `tests/app/test_jobs_api.py`

**Interfaces:**
- `list_input_files() -> list[InputFileResponse]` returns only regular `.csv` and `.xlsx` files below `INPUT_DIRECTORY`.
- `create_job(request: CreateJobRequest, session, queue) -> JobResponse` validates the selected path and enqueues one RQ task.
- `GET /api/files/inputs` lists available iQualif inputs.
- `GET /api/jobs/{job_id}/events` streams JSON event objects with `stage`, `completed`, `total`, and `status`.
- `GET /api/jobs/{job_id}/artifacts/{artifact_name}` serves only artifacts belonging to that job.

- [ ] **Step 1: Write path-safety tests.**

```python
def test_input_listing_rejects_path_escape(tmp_path):
    service = FileService(input_dir=tmp_path / "inputs", output_dir=tmp_path / "outputs")
    assert service.validate_input_path(tmp_path / "../secret.csv") is False
```

- [ ] **Step 2: Implement safe file services.**

Resolve paths and require them to remain under the configured input directory. Create a unique output directory per job, copy the source file as a raw artifact, and calculate SHA-256 checksums for input and output files.

- [ ] **Step 3: Write API validation tests.**

Cover missing input files, unsupported extensions, invalid workflows, and successful job creation. Mock the RQ queue and assert exactly one job is enqueued.

- [ ] **Step 4: Implement repository and API routes.**

Use Pydantic response schemas. Return HTTP 404 for unknown jobs and HTTP 422 for invalid configuration. Keep output paths server-owned; never accept an arbitrary download path from the client.

- [ ] **Step 5: Implement SSE progress streaming.**

Poll the job row at a bounded interval until a terminal status, yielding `text/event-stream` events. Close the stream on completion, failure, or cancellation.

- [ ] **Step 6: Run focused API tests.**

Run: `pytest tests/app/test_file_service.py tests/app/test_jobs_api.py -q`

- [ ] **Step 7: Commit the API slice.**

```bash
git add app tests/app
git commit -m "feat: add lead job and artifact APIs"
```

### Task 3: Worker and existing-pipeline integration

**Files:**
- Create: `app/services/pipeline_runner.py`, `app/worker.py`
- Modify: `src/reswip_leads/pipeline.py` only if a progress/evidence seam is required
- Test: `tests/app/test_pipeline_runner.py`, `tests/app/test_worker.py`

**Interfaces:**
- `PipelineJobConfig(input_path: str, output_path: str, profile_path: str, enricher: str, use_kbo: bool, use_pappers_fallback: bool, deduplicate: bool)`.
- `run_pipeline_job(job_id: str, config: PipelineJobConfig) -> None` updates the persisted job and never raises an unrecorded exception.
- `PipelineRunner.run(config, progress_callback) -> PipelineRunSummary`.

- [ ] **Step 1: Write runner tests with fake pipeline collaborators.**

Assert that progress updates use the stages `import`, `classify`, `verify`, `enrich`, `dedupe`, and `export`; existing non-empty contact fields stay unchanged; and a pipeline error marks the job failed with a message.

- [ ] **Step 2: Define the runner boundary.**

Construct the existing `LeadPipeline` with its injected importer, KBO verifier/web enricher, and Pappers fallback. Use the existing `EnrichmentResult`/`Evidence` contract. Keep source-specific scraping code out of the FastAPI routes.

- [ ] **Step 3: Add progress persistence.**

The callback receives `(stage_name, completed, total)`, updates one `JobStage`, and updates aggregate job counters. Avoid committing for every row; batch progress writes at a bounded interval and always write the final stage state.

- [ ] **Step 4: Implement the RQ entry point.**

Load the job by ID, transition `queued → running`, call the runner, store row outcomes/evidence, copy the final artifacts, and transition to `completed`, `completed_with_warnings`, or `failed`. Catch exceptions and persist the traceback summary without exposing secrets.

- [ ] **Step 5: Run focused worker tests.**

Run: `pytest tests/app/test_pipeline_runner.py tests/app/test_worker.py -q`

- [ ] **Step 6: Run existing pipeline regression tests.**

Run: `pytest tests/test_pipeline.py tests/test_enrichment.py tests/test_kbo_verifier.py -q`

- [ ] **Step 7: Commit the worker integration.**

```bash
git add app tests/app src/reswip_leads/pipeline.py
git commit -m "feat: run lead enrichment jobs in background"
```

### Task 4: React frontend and job wizard

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `frontend/src/types.ts`
- Create: `frontend/src/pages/DashboardPage.tsx`, `frontend/src/pages/NewJobPage.tsx`, `frontend/src/pages/JobsPage.tsx`
- Create: `frontend/src/components/AppShell.tsx`, `frontend/src/components/NewJobWizard.tsx`, `frontend/src/components/ui/*`
- Test: `frontend/src/pages/NewJobPage.test.tsx`, `frontend/src/api/client.test.ts`

**Interfaces:**
- `createJob(request: CreateJobRequest): Promise<Job>`.
- `listInputFiles(): Promise<InputFile[]>`.
- `getJobs(): Promise<JobSummary[]>`.
- `NewJobWizard` emits one validated `CreateJobRequest` on submit.

- [ ] **Step 1: Scaffold the Vite React TypeScript app.**

Configure Tailwind CSS, shadcn/ui primitives, React Router, Vitest, and Testing Library. Add an API base URL setting for local Docker and development-server use.

- [ ] **Step 2: Write wizard tests first.**

Cover the two workflow choices, required input selection for `enrich_existing`, required source/category fields for `scrape_new`, and disabled submit while the request is pending.

- [ ] **Step 3: Implement the shared API client and types.**

Keep API response parsing centralized. Represent job statuses and stage names as TypeScript unions matching the backend schemas.

- [ ] **Step 4: Implement the application shell and dashboard.**

Add navigation for Dashboard, New Job, Jobs, Databases, and Exports. The dashboard shows recent jobs, status badges, and counts without pretending that a job completed until the API says so.

- [ ] **Step 5: Implement the five-step New Job wizard.**

Support existing-file selection from `/api/files/inputs`, profile selection, KBO/name/Pappers/dedupe options, review, and submission. Do not expose proxy configuration in the first UI unless the backend setting is explicitly configured.

- [ ] **Step 6: Run frontend tests and build.**

Run: `cd frontend && npm test -- --run && npm run build`

- [ ] **Step 7: Commit the frontend wizard.**

```bash
git add frontend
git commit -m "feat: add lead job creation frontend"
```

### Task 5: Job progress, results, and artifacts UI

**Files:**
- Create: `frontend/src/pages/JobDetailPage.tsx`, `frontend/src/components/StageProgress.tsx`, `frontend/src/components/ResultSummary.tsx`, `frontend/src/components/ErrorTable.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/api/client.ts`
- Test: `frontend/src/pages/JobDetailPage.test.tsx`

**Interfaces:**
- `getJob(jobId: string): Promise<JobDetail>`.
- `subscribeToJob(jobId: string, onEvent: (event: JobEvent) => void): () => void`.
- `JobDetailPage` renders terminal results and artifact download links from server-provided names only.

- [ ] **Step 1: Write job-detail tests.**

Cover queued, running, completed-with-warnings, failed, and cancelled states. Assert that an SSE reconnect refreshes the job instead of resetting progress, and that blocked/unverified rows are shown as review items.

- [ ] **Step 2: Implement SSE subscription and fallback refresh.**

Use `EventSource`, close it on unmount, refresh the complete job when the stream closes unexpectedly, and stop polling after a terminal state.

- [ ] **Step 3: Implement stage progress and result summary.**

Show import, classify, KBO verification, enrichment, deduplication, and export separately. Show total rows, active companies, names found, missing names, failed lookups, and review count from API counters.

- [ ] **Step 4: Implement safe artifact downloads and error review.**

Render links only from the API artifact list. Show source, evidence URL, confidence, and error reason for row-level outcomes when present.

- [ ] **Step 5: Run frontend tests and build.**

Run: `cd frontend && npm test -- --run && npm run build`

- [ ] **Step 6: Commit the progress/results UI.**

```bash
git add frontend
git commit -m "feat: show lead job progress and results"
```

### Task 6: Docker Compose, local smoke test, and documentation

**Files:**
- Create: `Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`, `.env.example`
- Create: `scripts/run_ui_smoke_test.sh`
- Modify: `README.md`
- Test: `tests/app/test_health.py`

**Interfaces:**
- `GET /health` returns `{"status": "ok"}` without requiring the worker.
- Services: `api`, `worker`, `redis`, `postgres`, and `frontend`.

- [ ] **Step 1: Write the health endpoint test.**

```python
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Add Docker images and Compose services.**

Run the API with Uvicorn, the worker with `rq worker lead-acquisition`, and mount the configured Google Drive-backed input/output directory into API and worker containers at the same container path. Add health checks for PostgreSQL and Redis.

- [ ] **Step 3: Add environment documentation.**

Document `INPUT_DIRECTORY`, `OUTPUT_DIRECTORY`, `DATABASE_URL`, `REDIS_URL`, `DEFAULT_PROFILE`, and the frontend API URL. Explain that the Google Drive directory must be mounted or synchronized on the host before starting Compose.

- [ ] **Step 4: Add the smoke-test script.**

The script waits for `/health`, lists inputs, creates a job against a small fixture CSV, waits for a terminal job state, verifies that a new output artifact exists, and exits nonzero for failed or missing output.

- [ ] **Step 5: Run the complete verification set.**

Run:

```bash
pytest tests/app tests/test_pipeline.py tests/test_enrichment.py tests/test_kbo_verifier.py -q
cd frontend && npm test -- --run && npm run build
cd .. && docker compose config
```

Run the smoke test only with a small approved fixture, never the full Google Drive database during development.

- [ ] **Step 6: Update the README and commit the deployable MVP.**

```bash
git add Dockerfile frontend/Dockerfile docker-compose.yml .env.example scripts/run_ui_smoke_test.sh README.md tests/app
git commit -m "feat: package lead acquisition UI locally"
```

## Plan Self-Review

- Spec coverage: workflow selection, input listing, source configuration, background jobs, progress, artifacts, evidence, failure states, preservation rules, RQ/Redis, PostgreSQL, React UI, Docker Compose, and verification are covered by Tasks 1–6.
- Placeholder scan: no TBD/TODO or unspecified implementation step remains.
- Type consistency: the `JobStatus`, workflow values, `CreateJobRequest`, `PipelineJobConfig`, `PipelineRunSummary`, and SSE event fields are defined before their consumers.
- Scope: CRM write-back, billing, complex roles, and broad source marketplace remain explicitly outside the MVP.
