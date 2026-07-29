# Hainaut Three-Row Enrichment Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Pappers-first, KBO-web-second, and TVA-based Infobel fallback enrichment into the main lead pipeline and validate it on three Hainaut iQualif rows.

**Architecture:** Extend `LeadPipeline` with an optional Infobel batch enricher. Run Pappers and KBO web per lead in the configured order, then prefetch Infobel once for leads with incomplete contact data and merge only empty fields. Keep KBO status separate and authoritative; generate pilot files outside the source database.

**Tech Stack:** Python 3.12+, dataclasses, CSV, pytest, existing requests/Playwright enrichers.

## Global Constraints

- The original Hainaut CSV must remain unchanged.
- The pilot must contain exactly three valid-TVA leads.
- Existing non-empty values must never be overwritten.
- Infobel is a secondary contact verification/enrichment source, not the authority for legal company status.
- Missing values remain blank when no reliable evidence exists.
- No full 1,000-row live run until the pilot is reviewed.

---

### Task 1: Add failing pipeline contract tests

**Files:**
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Test the existing `LeadPipeline` constructor with a new `infobel` collaborator.
- Test an Infobel collaborator exposing `enrich_batch(leads)` and `enrich(tva, company_name)`.

- [ ] **Step 1: Write failing tests for source order and Infobel fallback**

Add tests that use recording fakes and assert:

```python
assert calls == ["pappers:BE...", "kbo_web:BE...", "infobel_batch"]
assert lead.first_name == "PappersFirst"
assert lead.position == "Director"
assert lead.email == "kbo@example.test"
assert lead.phone == "infobel-phone"
assert lead.status == "AC"
```

Also add tests proving an existing email is not overwritten and Infobel is not
asked to enrich a lead whose email, phone, and website are already complete.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```bash
PYTHONPATH=src pytest tests/test_pipeline.py -q
```

Expected failure: `LeadPipeline.__init__` does not accept `infobel`, and the
current enrichment loop does not call `enrich_batch`.

### Task 2: Wire the production enrichment order and fallback

**Files:**
- Modify: `src/reswip_leads/pipeline.py`
- Modify: `src/reswip_leads/enrichment/infobel.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- `LeadPipeline(..., pappers=None, kbo_web=None, infobel=None, ...)` stores the
  optional Infobel collaborator.
- `_stage_enrich()` invokes `infobel.enrich_batch(leads)` once after Pappers and
  KBO web processing when Infobel is configured.
- The per-lead merge uses the existing `_fill_if_empty` policy.

- [ ] **Step 1: Add the `infobel` constructor parameter and batch call**

Call `enrich_batch` only for leads with a TVA and at least one missing value
among email, phone, and website. Then call `enrich` per eligible lead and merge
email, phone, website, and source URL without overwriting existing values.

- [ ] **Step 2: Change the per-lead order to Pappers then KBO web**

Keep Pappers first so it discovers names and positions. KBO web runs second and
fills missing fields. Do not alter the no-overwrite behavior.

- [ ] **Step 3: Preserve KBO status separately**

When KBO returns a status, map it into `lead.status` and retain the raw KBO
status in `lead.kbo_status` where available. Infobel and Pappers must not modify
these fields.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
PYTHONPATH=src pytest tests/test_pipeline.py -q
```

Expected: all pipeline tests pass, including ordering, fallback eligibility,
status, and no-overwrite tests.

### Task 3: Add pilot selection and isolated files

**Files:**
- Create: `scripts/run_hainaut_three_row_pilot.py`
- Create: `tests/test_hainaut_pilot.py`

**Interfaces:**
- Script accepts the supplied source CSV and optional output directory.
- It selects three rows with valid TVA values, writes a pilot input CSV, runs
  the pipeline, and writes a separate pilot output CSV plus a JSON summary.

- [ ] **Step 1: Write failing tests for deterministic three-row selection**

Test that the selector reads semicolon-delimited UTF-8-with-BOM input, returns
exactly three rows with normalized TVA values, and leaves the source unchanged.

- [ ] **Step 2: Run the pilot tests and verify they fail for the missing script**

Run:

```bash
PYTHONPATH=src pytest tests/test_hainaut_pilot.py -q
```

- [ ] **Step 3: Implement isolated pilot selection and execution**

Use a fixed selection from valid source rows, write files under a dedicated
pilot directory, and pass `--enricher both` plus the Infobel collaborator in
the script. Do not write to the Google Drive source path.

- [ ] **Step 4: Run the pilot unit tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_hainaut_pilot.py -q
```

Expected: all pilot tests pass.

### Task 4: Execute and inspect the live three-row pilot

**Files:**
- Create: `/tmp` or a dedicated ignored pilot output directory only

- [ ] **Step 1: Run the pilot with the actual Hainaut source**

Use the supplied Google Drive CSV as read-only input and write pilot artifacts
to a separate local output directory. Use the persistent Infobel profile only
after confirming the browser session is available.

- [ ] **Step 2: Inspect all three rows and source metrics**

Review company identity, first name, last name, position, email, phone,
website, status, TVA, source URLs, and errors for every row.

- [ ] **Step 3: Fix one demonstrated defect at a time**

For each defect, add or update a focused failing test before changing production
code, rerun the focused test, then rerun the pilot.

- [ ] **Step 4: Run the complete relevant test suite**

Run:

```bash
PYTHONPATH=src pytest tests/test_pipeline.py tests/test_enrichment.py tests/test_infobel.py -q
```

Record the exact pass/fail counts and any remaining external Cloudflare issue.

### Task 5: Commit the verified implementation

- [ ] **Step 1: Review the diff and ensure only requested files are staged**
- [ ] **Step 2: Commit with message `feat: wire staged lead enrichment fallbacks`**
- [ ] **Step 3: Report pilot paths, counts, source outcomes, tests, and unresolved issues**
