# Plan: Pipeline Orchestration (Milestone 5)

## Context

The repository is being built as a sector-neutral Belgian B2B lead-acquisition
platform. Each stage of the pipeline already has a module with stub
implementations:

- `sources/iqualif/importer.py` — `IQualifImporter.import_leads()`
- `verification/kbo/zip_reader.py` — `KboZipReader.build_index()`
- `verification/kbo/verifier.py` — `KboVerifier.verify()`
- `enrichment/pappers.py` — `PappersEnricher.enrich()` (stub)
- `enrichment/kbo_web.py` — `KboWebEnricher.enrich()` (stub)
- `enrichment/base.py` — `BaseEnricher` (abstract)
- `deduplication/dedupe.py` — `deduplicate()`
- `core/fields.py` — `classify_province`, `classify_region`, `classify_language`
- `core/profile.py` — `load_profile()`
- `exports/zoho.py` — `export_csv()`, `export_xlsx()`
- `verification/kbo/downloader.py` — `KboDownloader` (added in M4)

What is **missing** is the module that wires them together. Without a
pipeline orchestrator the modules are isolated and unusable as a product.
This milestone creates that single entry point.

## Outcome

A new `src/reswip_leads/pipeline.py` module that:

1. Takes a sector profile name, a list of source CSV paths, and an output path.
2. Runs the canonical 6-stage flow.
3. Returns a `PipelineResult` with per-stage metrics so an operator can
   see what happened.
4. Is fully dependency-injected — every I/O and network boundary is
   passed in, so tests never hit the network and never read the real disk.
5. Exposes a CLI: `python -m reswip_leads.pipeline --profile energy --input ... --output ...`

The flow to implement (from the README and M4 closing block):

```
import → verify(KBO) → enrich → classify(region/language) → deduplicate → export
```

## Files to Create

- `src/reswip_leads/pipeline.py` — orchestrator (new)
- `tests/test_pipeline.py` — unit tests (new)

## Files to NOT modify

- `src/reswip_leads/enrichment/*` — reserved for Agent 2 / Milestone 6
- Anything else outside the scope

## Design

### Public types

```python
@dataclass
class PipelineStageMetrics:
    name: str
    input_count: int
    output_count: int
    errors: List[str] = field(default_factory=list)
    notes: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PipelineResult:
    profile: str
    output_path: str
    stages: List[PipelineStageMetrics]
    leads: List[Lead]              # final exported leads
    duration_seconds: float
    success: bool
    error: str = ""
```

### Public API

```python
class LeadPipeline:
    def __init__(
        self,
        profile: Profile,
        output_path: str,
        kbo_zip_path: Optional[str] = None,   # if None, KBO verification is skipped
        kbo_reader: Optional[KboZipReader] = None,
        kbo_verifier: Optional[KboVerifier] = None,
        pappers: Optional[PappersEnricher] = None,
        kbo_web: Optional[KboWebEnricher] = None,
        importer: Optional[IQualifImporter] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> None: ...

    def run(self) -> PipelineResult: ...

def run_pipeline(
    profile_name: str,
    input_csvs: List[str],
    output_path: str,
    kbo_zip_path: Optional[str] = None,
    output_format: str = "csv",     # "csv" | "xlsx"
) -> PipelineResult: ...
```

### Stage implementation (in order)

1. **import** — call `importer.import_leads(input_csvs)`. If empty,
   short-circuit with an empty result. Records `imported_count` note.

2. **classify** — for each lead, fill `province`/`region`/`language` if
   missing using `core.fields.classify_*`. Pure transformation, no I/O.

3. **verify (KBO)** — if `kbo_zip_path` is given, build a TVA index via
   `KboZipReader.build_index(zip_path, targets=tv_as, activity_code="")`
   and call `KboVerifier.verify(tva)` for each. The verifier returns
   `status`, `company_name`, `address`, etc.; we **never overwrite**
   existing non-empty values (mirrors `_merge_into` in `dedupe.py`).
   Records counts in `notes` (`verified`, `inactive`, `not_found`, `error`).

4. **enrich (Pappers, then KBO web)** — for each lead with a valid TVA,
   call the configured enrichers in order. For now these are stubs and
   return no fields, so this stage is a no-op on real data but the
   wiring is exercised in tests via fake enrichers.

5. **deduplicate** — call `deduplicate(leads)`. Records
   `duplicates_removed` count.

6. **export** — call `export_csv(leads, output_path, profile)` or
   `export_xlsx(...)` based on `output_format`. Creates the output
   directory if missing. Records final `exported_count`.

### Error handling

- A single bad lead (e.g. invalid TVA) must not abort the pipeline.
- Stage errors are collected in `PipelineStageMetrics.errors` (e.g.
  `"Failed to enrich BE0123456789: connection timeout"`).
- If a stage raises an unexpected exception, the pipeline catches it,
  records the message, and continues. The final `PipelineResult.success`
  is `False` only if a stage produced zero output where output was
  expected (e.g. import returns nothing).

### Dependency injection

Every I/O boundary accepts an injected instance, with a default
production instance. This mirrors the pattern already established in
`KboDownloader(session=...)` and `KboVerifier`.

- `importer` → `IQualifImporter()`
- `kbo_reader` → `KboZipReader()`
- `kbo_verifier` → `KboVerifier()`
- `pappers` → `PappersEnricher()` (None means skip Pappers)
- `kbo_web` → `KboWebEnricher()` (None means skip KBO web)

The progress callback (`Optional[Callable[[str, int, int], None]]`) lets
callers report progress without coupling the pipeline to logging.

### CLI

```python
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="reswip_leads.pipeline")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--input", required=True, nargs="+", help="One or more source CSV files.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--kbo-zip", default="", help="Optional KBO Open Data ZIP for verification.")
    parser.add_argument("--format", choices=["csv", "xlsx"], default="csv")
    args = parser.parse_args(argv)
    result = run_pipeline(
        profile_name=args.profile,
        input_csvs=args.input,
        output_path=args.output,
        kbo_zip_path=args.kbo_zip or None,
        output_format=args.format,
    )
    print(f"Pipeline {'succeeded' if result.success else 'failed'} in {result.duration_seconds:.2f}s")
    for stage in result.stages:
        print(f"  {stage.name}: {stage.input_count} → {stage.output_count}")
    return 0 if result.success else 1
```

## Tests

`tests/test_pipeline.py` with the following test classes. **No real
network, no real disk reads** — every collaborator is faked.

- `TestRunPipelineHappyPath`
  - Builds 3 leads, no KBO zip, runs the pipeline, expects a CSV on disk
    with 3 rows, all stages report `success=True`, `result.success=True`.

- `TestStageOrdering`
  - Wraps every collaborator with a recording fake that logs the call
    order; asserts `import → classify → verify → enrich → dedupe → export`.

- `TestVerifyStage`
  - With a fake `KboZipReader` returning a record for one TVA, asserts
    that the corresponding lead gets `company_name` and `address`
    filled from KBO evidence, and that an existing non-empty value on
    the lead is **not** overwritten.
  - With no `kbo_zip_path`, the verify stage is a no-op pass-through.

- `TestEnrichStage`
  - With a fake `PappersEnricher` returning `first_name="Jean"`,
    asserts the lead gets the value; with an existing `first_name`
    already set, the existing value is preserved.
  - With `pappers=None` the stage is skipped.

- `TestClassifyStage`
  - Lead with `province="Hainaut"` gets `region="Wallonia"` and
    `language="FR"` filled.
  - Existing non-empty `region` is not overwritten.

- `TestDedupeStage`
  - Two leads with the same TVA collapse to one; `duplicates_removed`
    is recorded in stage notes.

- `TestExportStage`
  - With `output_format="csv"`, file is written and has a header row
    with the Zoho columns.
  - With `output_format="xlsx"` and openpyxl missing, fallback to CSV
    is exercised.

- `TestErrorHandling`
  - A stage that raises an exception records the error in
    `PipelineStageMetrics.errors` and the pipeline continues.
  - When `import` returns an empty list, `result.success=False` and
    `result.error` is populated.

- `TestDependencyInjection`
  - Passing custom `importer`, `kbo_reader`, `kbo_verifier`, `pappers`,
    `kbo_web` is the only path used — no module-level singletons.

- `TestCLI`
  - `python -m reswip_leads.pipeline --profile energy --input x.csv --output y.csv`
    via `subprocess` returns 0 and produces `y.csv`.

## Verification

```bash
PYTHONPATH=src pytest tests/test_pipeline.py -v
PYTHONPATH=src pytest tests/ -q          # full suite must remain green
python -m reswip_leads.pipeline --profile energy --input data/sample.csv --output /tmp/out.csv
```

The full suite (104 existing + ~10 new pipeline tests) must pass.

## Out of scope (intentionally)

- Live network calls in `PappersEnricher` / `KboWebEnricher` —
  reserved for Milestone 6.
- Parallel execution / worker pools — single-threaded is correct for
  v1 and easy to reason about.
- Checkpoint / resume — the data set for a single profile is small
  enough to re-run.
- Logging framework integration — callers can use the `progress` hook.
- Caching of KBO index between runs — the user can pass the same
  `kbo_zip_path` if desired; the orchestrator does not manage caches.
