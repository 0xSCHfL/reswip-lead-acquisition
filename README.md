# Reswip Lead Acquisition Platform

General-purpose Belgian B2B lead-acquisition and enrichment platform for Reswip.

The project collects companies from sector-specific sources such as iQualif,
verifies company identity with the Belgian KBO using the TVA number, enriches
missing decision-maker information from reliable public sources, removes
duplicates, and exports CRM-ready databases for prospecting campaigns.

This project is intentionally sector-neutral. Insurance is one module; Energy
is the first active use case. New sectors should be added through profiles and
small adapters, not by rewriting the core pipeline.

## What the Platform Does

```text
Source database (iQualif or another provider)
        ↓
Normalize company fields
        ↓
Pappers: first/last name and position
        ↓
KBO ZIP: official verification, status, identity, and contacts
        ↓
KBO Web: fill remaining director/contact fields
        ↓
Infobel: targeted fallback for remaining missing fields
        ↓
Deduplicate companies
        ↓
Classify region, province, and language
        ↓
Export to Zoho CRM
        ↓
Prospecting calls and campaign reporting
```

The TVA number is the primary company key. Existing non-empty values are never
overwritten. Missing values remain empty when no reliable source provides them.

## First Use Case: Energy

The first workflow targets energy prospects in Wallonie. A typical profile
selects an iQualif category, region, language, CRM organization, and lead
source. The resulting database can be imported into Zoho and used for energy
prospecting calls.

## Enrichment Order and Source Authority

The pipeline uses the Belgian TVA number as the lookup key and enriches each
row according to its missing fields:

1. **Pappers** — preferred source for first name, last name, and position.
2. **KBO ZIP** — official offline verification, legal status, identity, address,
   activities, and any available company email/phone/website.
3. **KBO Web** — fills missing decision-maker and contact details.
4. **Infobel** — fallback verification/enrichment only for rows still missing
   email, phone, or website.

Pappers has priority for person identity. If Pappers and Infobel disagree,
the Pappers name is retained; Infobel can only fill blank fields. KBO is the
authority for company identity and legal status. The original iQualif database
is read-only: enrichment is written to a separate output file.

For multiple Infobel results, do not blindly select the first result. Match in
this order:

`exact TVA + exact normalized address` → `TVA + postcode` → `TVA + company name`

An address match is confirmed only after normalizing case, accents,
punctuation, and common street abbreviations. If candidates remain tied, mark
the result `infobel_ambiguous`; if no candidate matches, mark it
`infobel_no_result`.

From the repository root, set the source and output paths and run:

```bash
cd /home/sohaib/Work/projects/myP/Reswip-lead-acquisition
export PYTHONPATH=src

python3 -m reswip_leads.pipeline \
  --profile profiles/energy.yaml \
  --input /path/to/input.csv \
  --output /path/to/enriched.csv \
  --enricher both
```

Use only one source when needed:

```bash
# KBO web only (official register)
python3 -m reswip_leads.pipeline \
  --profile profiles/energy.yaml \
  --input /path/to/input.csv \
  --output /path/to/enriched_kbo.csv \
  --enricher kbo-web

# Pappers only (fallback/commercial directory)
python3 -m reswip_leads.pipeline \
  --profile profiles/energy.yaml \
  --input /path/to/input.csv \
  --output /path/to/enriched_pappers.csv \
  --enricher pappers
```

If a proxy list is required, pass one URL per line with `--proxy-file`:

```bash
python3 -m reswip_leads.pipeline \
  --profile profiles/energy.yaml \
  --input /path/to/input.csv \
  --output /path/to/enriched.csv \
  --enricher both \
  --proxy-file /path/to/proxies.txt
```

The pipeline also classifies Province/Region into Language and DB Region,
deduplicates by normalized TVA, and preserves the original input columns.
Review the output before CRM import. Do not invent a name or position from a
company name.

### Download and use a KBO bulk ZIP

The official bulk ZIP can be downloaded when a direct URL or URL template is
configured:

```bash
python3 -m reswip_leads.verification.kbo.downloader \
  --output-dir data/kbo \
  --url "$RESWIP_KBO_ZIP_URL"
```

Then add the ZIP to the pipeline for offline company verification:

```bash
python3 -m reswip_leads.pipeline \
  --profile profiles/energy.yaml \
  --input /path/to/input.csv \
  --kbo-zip data/kbo/KboOpenData_YYYY_YYYY_MM_DD_Full.zip \
  --output /path/to/enriched.csv \
  --enricher both
```

For batches, the ZIP is indexed once per pipeline run. The large activity file
is skipped unless NACE activity data is explicitly requested. This avoids
network requests for basic company verification and status.

### Recheck missing emails only

For a database that already has names and only needs public email discovery:

```bash
python3 -m reswip_leads.enrichment.email_recheck \
  --input /path/to/enriched.csv \
  --output /path/to/email_rechecked.csv \
  --source all \
  --missing-only
```

Email sources are checked only for missing email fields. Store the evidence URL
and review medium/low-confidence directory results before calling.

## Sector Modules

- **Energy** — iQualif energy categories, KBO verification, public contact
  enrichment, and energy CRM exports.
- **Insurance** — insurance-specific sources and FSMA licensing data, while
  reusing the same normalization, verification, deduplication, and export core.
- **Future sectors** — sector-specific filters and enrichment adapters can be
  added without changing the canonical lead model.

## Data Quality Rules

- Prefer TVA/KBO evidence for company identity.
- Preserve the original source data before normalization.
- Do not overwrite existing values without evidence.
- Keep decision-maker fields empty when no reliable public match exists.
- Deduplicate by normalized TVA number; retain branch information when needed.
- Keep source and verification evidence available for review.

## Structure

```
src/reswip_leads/
├── core/           # Canonical models, fields, validation, and profiles
├── sources/        # Data source integrations
│   └── iqualif/    # iQualif import and field mapping
├── verification/   # Business verification
│   └── kbo/        # KBO (Kruispuntbank) verification by TVA
├── enrichment/     # Decision-maker and public contact enrichment
├── deduplication/  # Company and branch deduplication
└── exports/        # Zoho CRM and other export formats

profiles/           # Industry-specific configs
tests/              # Test suite
docs/               # Documentation
data/               # Local raw/intermediate data (not committed)
```

## Database Storage Convention

Generated databases are organized outside the code repository by sector:

```text
Databases/<Sector>/Belgium/<Region>/
├── Raw/
├── Normalized/
├── Enriched/
├── CRM Ready/
└── Reports/
```

## Infobel Scraping (reCAPTCHA Auto-Solve)

Scrapes [Infobel Belgium](https://www.infobel.com/fr/belgium/) business details —
name, address, TVA, phone, email, hours, and financial data — by searching the
TVA when a row is incomplete.

### Setup

```bash
# 1. System deps (Ubuntu/Debian)
sudo apt update && sudo apt install -y ffmpeg chromium

# 2. Python venv
python3 -m venv venv
source venv/bin/activate

# 3. Install package + Playwright browser
pip install -e .
playwright install chromium
```

### Usage

```bash
# Collect detail URLs from a search, then scrape them
python -m reswip_leads.sources.infobel.collect_links "Restaurant" "Liège" -o links.csv
python -m reswip_leads.sources.infobel.scrape_urls links.csv

# Or run both in one command
python -m reswip_leads.sources.infobel.pipeline "Restaurant" "Liège" -o results.csv

# Search by TVA from a CSV
python -m reswip_leads.sources.infobel.pipeline \
  --input-csv /path/to/incomplete_tvas.csv \
  -o /path/to/infobel_results.csv \
  --headed \
  --profile-dir ~/.infobel-profile

# Headless (no display needed — works on any Linux VPS)
python -m reswip_leads.sources.infobel.collect_links "Restaurant" "Liège" -o links.csv --no-headed
```

### How the Solver Works

Infobel uses Cloudflare JS challenges + Google reCAPTCHA v2. The solver:

1. Clicks the reCAPTCHA checkbox
2. Switches to the **audio challenge** (no API key needed)
3. Clicks play so reCAPTCHA populates the audio URL
4. Downloads the MP3 via Playwright's request context (avoids CORS)
5. Converts to WAV via `ffmpeg`
6. Transcribes with Google Speech Recognition (`speech_recognition`)
7. Fills the response and clicks verify
8. Repeats on wrong answer (up to 5 attempts — retry on failure)

No Capsolver, no Gemini, no API keys. Requires `ffmpeg` on the system and an internet connection for Google Speech Recognition.

### Browser and abuse-page flow

The TVA fallback uses one Chromium context with two reusable tabs:

```text
Search tab → search TVA → find detail link
Detail tab → open link → scrape company
Search tab → next TVA
```

When an abuse page appears, the solver runs for that TVA. After the challenge
is cleared, the scraper waits for the detail link. If no matching link appears,
that TVA is recorded as `infobel_no_result`; it is not blindly retried.

For pilot runs, use the isolated runner. It writes the input, enriched CSV,
summary, and (for new runs) `run.log` under the output directory:

```bash
PYTHONPATH=src python3 scripts/run_hainaut_three_row_pilot.py \
  --source "/path/to/hainaut_iqualif.csv" \
  --output-dir /tmp/reswip-hainaut-10-row-pilot \
  --kbo-zip data/kbo/KboOpenData_YYYY_MM_DD_Full.zip \
  --profile-dir ~/.infobel-profile \
  --limit 10
```

Do not start the full 1,000-row live run until checkpoint/resume output is in
place. A timeout or browser interruption must not discard completed Infobel
results.

## Development Status

The staged enrichment pipeline, KBO ZIP verification, Infobel TVA fallback,
two-tab browser flow, pilot runner, and focused regression tests are in place.
The next milestone is resumable checkpoint processing for the full 1,000-row
database. The original insurance project remains separate and is not modified
by this repository.
