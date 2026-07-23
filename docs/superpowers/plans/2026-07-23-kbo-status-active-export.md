# KBO Status and Active Zoho Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve KBO activity status in the general lead database while exporting only active companies to Zoho CRM.

**Architecture:** Add a sector-neutral `kbo_status` field to `Lead`. KBO verification fills that field from the latest ZIP, while the general pipeline retains all rows. The Zoho export layer filters rows whose normalized status is not `active`, leaving archival outputs unchanged.

**Tech Stack:** Python dataclasses, CSV/XLSX exporters, pytest, existing `KboVerifier` and `KboZipReader`.

## Global Constraints

- Never delete inactive rows from raw, normalized, or enriched archives.
- Zoho CRM output contains active companies only.
- Empty or unknown status is excluded from the active-only CRM export for safety.
- Existing non-status fields must not be overwritten by KBO verification.

### Task 1: Lead status model and KBO verifier

**Files:**
- Modify: `src/reswip_leads/core/models.py`
- Modify: `src/reswip_leads/verification/kbo/verifier.py`
- Test: `tests/test_core.py`
- Test: `tests/test_kbo_verifier.py`

- [ ] Add `kbo_status: str = ""` to `Lead`, include it in `to_dict()`/`from_dict()`, and map CSV field names to `KBO Status`.
- [ ] In both single and batch KBO verification results, return the source enterprise status and set the lead’s `kbo_status` without changing existing contact fields.
- [ ] Add tests asserting active and inactive records preserve their exact KBO status and round-trip through the model.
- [ ] Run `PYTHONPATH=src pytest -q tests/test_core.py tests/test_kbo_verifier.py` and require all tests to pass.

### Task 2: Pipeline status propagation

**Files:**
- Modify: `src/reswip_leads/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] Update the KBO stage to copy `result["status"]` into `lead.kbo_status` for every verified, inactive, or not-found result.
- [ ] Ensure raw/normalized/enriched/clean outputs retain inactive rows and include `KBO Status`.
- [ ] Add tests proving an inactive lead remains in pipeline results and has `KBO Status=inactive`.
- [ ] Run `PYTHONPATH=src pytest -q tests/test_pipeline.py` and require all tests to pass.

### Task 3: Active-only Zoho export

**Files:**
- Modify: `src/reswip_leads/exports/zoho.py`
- Modify: `src/reswip_leads/exports/zoho_export.py`
- Test: `tests/test_zoho.py`

- [ ] Add `KBO Status` to the general/exportable row mapping where the schema permits it.
- [ ] Add a single helper that normalizes status values (`AC`, `active`, and `Actif` → active) and returns whether a lead is exportable.
- [ ] Filter only active leads in CSV/XLSX Zoho export; exclude empty, inactive, stopped, ceased, liquidation, and bankruptcy statuses.
- [ ] Keep Energy CRM column order and existing field behavior unchanged for active rows.
- [ ] Add tests with active, inactive, stopped, and blank statuses, asserting only active rows are exported.
- [ ] Run `PYTHONPATH=src pytest -q tests/test_zoho.py` and require all tests to pass.

### Task 4: Full regression and documentation

**Files:**
- Modify: `README.md`
- Test: `tests/`

- [ ] Document that archives retain all KBO statuses while Zoho exports active companies only.
- [ ] Run `PYTHONPATH=src pytest -q` and require the complete suite to pass.
- [ ] Inspect a small fixture export and confirm the inactive row is absent from Zoho output but present in the enriched/archive output.
- [ ] Commit with `git add src tests README.md docs/superpowers/plans/2026-07-23-kbo-status-active-export.md && git commit -m "feat: preserve KBO status and export active leads only"`.
