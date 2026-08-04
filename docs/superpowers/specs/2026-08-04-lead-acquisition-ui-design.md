# Lead Acquisition UI Design

## Goal

Provide a browser-based interface for two workflows:

1. Enrich an existing iQualif database with KBO company status and publicly verifiable first name, last name, and position.
2. Create a new company database from a supported source, then run the same verification and enrichment pipeline.

The original input must remain unchanged. Every output value should retain its source and evidence where available.

## MVP scope

The first release includes:

- A dashboard showing recent jobs and their status.
- A New Job wizard with two workflow choices: Enrich Existing File and Scrape New Companies.
- Input selection from the configured Iqualif Google Drive directory, plus upload support.
- Source and filter configuration for new scraping jobs.
- Options for KBO status verification, representative enrichment, Pappers fallback, deduplication, and CSV export.
- Background execution with progress by pipeline stage.
- A job detail page with counts, errors, failed/unverified rows, and downloadable output files.
- Evidence URL/source fields and a review status for enriched person data.

The MVP does not include CRM write-back, complex user roles, billing, or a broad source marketplace.

## User experience

The application has a left navigation with Dashboard, New Job, Jobs, Databases, Exports, and Settings.

The New Job wizard has these steps:

1. Choose workflow.
2. Select the input file or scraping source.
3. Configure sector, region, category, language, and record limit when applicable.
4. Select enrichment options.
5. Review the configuration and start the job.

The job page displays stage progress for source collection, normalization, KBO verification, name enrichment, deduplication, and export. Results include total rows, active companies, names found, missing names, failed lookups, and records needing review.

## Architecture

The React/TypeScript frontend calls a FastAPI backend. FastAPI creates a job record and enqueues work in RQ backed by Redis. A worker invokes the existing Python source adapters and pipeline modules. PostgreSQL stores users, jobs, configuration, stage progress, row-level outcomes, and evidence metadata. CSV inputs and outputs remain in the configured Google Drive-backed filesystem; PostgreSQL stores their paths and checksums.

The frontend receives progress through Server-Sent Events initially. The API remains the source of truth, so reconnecting to a job page does not lose progress.

## Enrichment rules

- TVA/VAT is the primary company matching key.
- KBO is the primary source for company status and mandate-holder names.
- Pappers is a bounded fallback when KBO has no usable person data.
- Existing first name, last name, and position values are preserved.
- Unverified names remain blank; timeouts and blocked pages never become data.
- Each enriched value records its source, evidence URL, confidence, and timestamp when available.
- The original file is never overwritten; every run creates a new output artifact.

## Initial data model

- `users`: account and authentication metadata.
- `jobs`: workflow, input/output paths, configuration, status, timestamps, and counters.
- `job_stages`: stage status, progress, error summary, and timestamps.
- `job_records`: input row identifier, TVA, outcome, review state, and error details.
- `evidence`: field, value source, URL, confidence, and captured timestamp.

## Error handling

Jobs support queued, running, completed, completed_with_warnings, failed, and cancelled states. Network failures use bounded retries and rate limits. A blocked or unavailable source is reported as blocked/unverified and does not produce guessed values. Partial results remain downloadable when safe.

## Technology choices

- Backend: Python FastAPI.
- Background jobs: RQ and Redis for the first release.
- Frontend: React and TypeScript.
- UI: Tailwind CSS and shadcn/ui.
- Database: PostgreSQL.
- Browser scraping: existing Playwright adapters where required.
- Deployment: Docker Compose.

Celery is deferred unless the workflow later needs substantially more complex orchestration than RQ provides.

## Verification

Backend tests cover job creation, authorization, state transitions, retry behavior, evidence preservation, and output paths. Pipeline tests continue to cover the existing enrichers. Frontend tests cover wizard validation, progress rendering, reconnect behavior, and result summaries. A local smoke test runs one small input file end-to-end before larger enrichment jobs are enabled.
