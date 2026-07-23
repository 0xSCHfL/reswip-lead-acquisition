# Email Recheck Enrichment — Design Spec

**Date:** 2026-07-23
**Status:** Approved
**Goal:** Recheck only rows where the Email field is empty. Use KBO first, then Pappers, then the official company website.

## Architecture: Source-Based (Approach B)

Independent source classes orchestrated by a single enricher. Both pipeline integration (`--enricher email`) and standalone CLI use the same `EmailRecheckEnricher`.

## Data Model

### EmailCandidate

```python
@dataclass
class EmailCandidate:
    email: str
    source: str           # "kbo_zip", "kbo", "pappers", "website"
    source_url: str       # URL where email was found
    confidence: str       # "High", "Medium", "Low"
    note: str             # Short verification note
```

`None` return from a source means no email found.

### BaseEmailSource

```python
class BaseEmailSource(ABC):
    @abstractmethod
    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None  # requests-style: {"http": "...", "https": "..."}
    ) -> Optional[EmailCandidate]:
        ...
```

## Source Classes

### KboZipSource

- Uses injected `KboZipReader` instance (no network request)
- Looks up contact data by normalized TVA
- Extracts email from contact fields
- Confidence: **High**

### KboEmailSource

- Scrapes `kbopub.economie.fgov.be` by TVA
- Extracts from `mailto:` links, then regex fallback
- Filters `kbopub`/`economie` addresses
- Confidence: **High**

### PappersEmailSource

- Scrapes `pappers.be` company page
- Decodes Cloudflare email protection (`data-cfemail`)
- Filters `pappers` addresses
- Confidence: **Medium**

### WebsiteEmailSource

- Fetches official company website (uses `website_url` from lead/KBO/Pappers)
- Tries `requests.get()` first; falls back to Playwright only if response is JS-heavy (<500 bytes, `<noscript>`, `window.location`, empty `<div id="app">`)
- Extracts emails via regex from rendered HTML
- Filters generic/noreply emails
- Confidence: **Low**

## Source Priority Chain

1. KBO ZIP contact data (when `--kbo-zip` provided, injected `KboZipReader`)
2. KBO web scrape
3. Pappers scrape
4. Official company website (uses `website_url` from lead or KBO/Pappers result)

`EmailRecheckEnricher` iterates sources in order. Returns first valid email found.

## EmailRecheckEnricher

**File:** `src/reswip_leads/enrichment/email_recheck.py`

```python
class EmailRecheckEnricher(BaseEnricher):
    SOURCE_NAME = "email_recheck"

    def __init__(self, config, sources=None, missing_only=True,
                 kbo_zip_reader=None):
        # sources: list of BaseEmailSource instances
        # default: [KboZipSource(kbo_zip_reader), KboEmailSource(), PappersEmailSource(), WebsiteEmailSource()]
        # missing_only: when True, only process leads with empty email
        self._current_website_url = ""  # set per-lead before enrich()

    def set_lead_context(self, lead):
        """Store per-lead context before enrich() call."""
        self._current_website_url = getattr(lead, 'website', '') or ''

    def enrich(self, tva, company_name="") -> Dict[str, Any]:
        """Matches BaseEnricher.enrich() signature."""
        website_url = self._current_website_url
        for source in self.sources:
            candidate = source.find_email(tva, company_name, website_url, self.config.proxy)
            if candidate and _is_valid_email(candidate.email, website_url):
                return {"email": candidate.email, "_email_candidate": candidate}
        return {}

    def apply_to_lead(self, lead, result):
        if lead.email:
            return  # never overwrite
        lead.email = result.get("email", "")
        if not lead.email1 and lead.email:
            lead.email1 = lead.email
```

**Pipeline integration:** Before calling `enricher.enrich()`, the pipeline calls `enricher.set_lead_context(lead)` to pass the lead's website URL. This avoids changing `BaseEnricher.enrich()` signature.

## Email Validation

### `_is_valid_email(email, website_url="")` 

Reject:
- `noreply@`, `no-reply@`, `donotreply@`
- Domains: `example.com`, `test.com`, `localhost`
- Domains: `pappers.be`, `kbopub.economie.fgov.be`, `google.com`, `facebook.com`, `linkedin.com`, `twitter.com`, `instagram.com`

Accept `info@domain` when `domain` matches the official website domain.

### Domain confidence rules

- KBO/Pappers email tied to same TVA → accept (High/Medium)
- Website email on supplied official domain → accept
- `info@` on different but plausible brand/parent domain → accept with Medium/Low confidence, record mismatch in note
- Reject only clearly unrelated directory/social domains

## Pipeline Integration

- Add `--enricher email` to CLI (alongside pappers/kbo-web/both/none)
- In `_build_enrichers()`, instantiate `EmailRecheckEnricher` when `--enricher email`
- Pass `kbo_zip_reader` when `--kbo-zip` is provided
- In `_stage_enrich()`, before calling `enricher.enrich()`, call `enricher.set_lead_context(lead)` to pass website URL
- The enricher processes all leads but `missing_only=True` skips non-empty emails

## Standalone CLI

```bash
PYTHONPATH=src python3 -m reswip_leads.enrichment.email_recheck \
  --input input.csv \
  --output output.csv \
  --missing-only \          # default: True
  --source kbo|pappers|website|all \  # default: all
  --proxy-file proxies.txt \
  --timeout 30 \
  --retries 3 \
  --delay 1.0
```

Behavior:
- Loads CSV via existing `IQualifImporter`
- Filters rows where `email` is empty (unless `--no-missing-only`)
- Runs `EmailRecheckEnricher` with selected sources
- Writes enriched CSV (all original columns preserved)
- Writes `email_recheck_report.csv` alongside output

## Evidence & Report

### Report columns

| Column | Description |
|--------|-------------|
| TVA | Normalized TVA |
| Company Name | Business name |
| Email | Found email (empty if none) |
| Source | kbo_zip / kbo / pappers / website |
| Source URL | URL where email was found |
| Confidence | High / Medium / Low |
| Note | Short verification note |
| Status | Email Found / No Reliable Public Email / Invalid Email / Source Error |

### Status rules

- **Email Found:** Valid email extracted and accepted
- **No Reliable Public Email:** All sources checked, no valid email found
- **Invalid Email:** Email found but rejected by validation
- **Source Error:** Network/parse error prevented lookup

## Output Rules

- Preserve all original columns
- Never overwrite existing Email values
- Preserve Email 1 if it already exists
- If new email found and Email 1 is empty → copy to Email 1
- Keep original row order

## Testing

**File:** `tests/test_email_recheck.py`

| Test | Description |
|------|-------------|
| `test_kbo_zip_preferred` | KBO ZIP has email → highest priority, no network |
| `test_kbo_email_preferred` | KBO web returns email → used, Pappers not called |
| `test_pappers_fallback` | KBO empty → Pappers email used |
| `test_website_fallback` | KBO+Pappers empty → website email used |
| `test_existing_email_preserved` | Lead already has email → not overwritten |
| `test_generic_email_rejected` | noreply@, example.com, facebook.com rejected |
| `test_info_at_accepted` | info@company-be.com accepted when domain matches |
| `test_info_at_domain_mismatch` | info@other.com → Low confidence, mismatch noted |
| `test_missing_email_blank` | No source finds email → stays empty |
| `test_missing_only_filter` | Only empty-email rows processed |
| `test_source_url_recorded` | Each candidate has correct source_url |
| `test_confidence_levels` | KBO=High, Pappers=Medium, Website=Low |
| `test_report_generation` | Report CSV written with correct statuses |
| `test_standalone_cli` | CLI loads CSV, filters, enriches, writes output |
| `test_pipeline_enricher_email` | `--enricher email` works in pipeline |
| `test_no_live_network` | All tests use mocked HTTP responses |

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/reswip_leads/enrichment/email_sources.py` | **Create** — BaseEmailSource, EmailCandidate, KboZipSource, KboEmailSource, PappersEmailSource, WebsiteEmailSource |
| `src/reswip_leads/enrichment/email_recheck.py` | **Create** — EmailRecheckEnricher, standalone CLI, report generation |
| `src/reswip_leads/pipeline.py` | **Modify** — Add `--enricher email`, wire EmailRecheckEnricher in `_build_enrichers()` |
| `tests/test_email_recheck.py` | **Create** — All tests with mocked HTTP |
| `tests/fixtures/` | **Create** — HTML snippets for KBO/Pappers pages with/without emails |
