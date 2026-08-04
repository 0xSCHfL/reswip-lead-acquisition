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
Verify company with TVA/KBO
        ↓
Enrich director or manager when publicly verifiable
        ↓
Deduplicate companies
        ↓
Classify region, province, and language
        ↓
Export to Zoho CRM
        ↓
Prospecting calls and campaign reporting
```

The TVA number is the primary company key. First Name, Last Name, and Position
are optional because iQualif generally provides company data, not a reliable
decision-maker identity. Missing names must remain empty rather than being
invented.

## First Use Case: Energy

The first workflow targets energy prospects in Wallonie. A typical profile
selects an iQualif category, region, language, CRM organization, and lead
source. The resulting database can be imported into Zoho and used for energy
prospecting calls.

## Enrich First Name, Last Name, and Position

The decision-maker enrichment uses the Belgian TVA number as the lookup key.
KBO web is the official source; Pappers is used as a fallback when KBO does
not expose a usable function holder. Existing values are never overwritten, and
missing values remain empty when neither source returns reliable evidence.

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
Review the output before CRM import. Pappers-only matches should be treated as
medium confidence; do not invent a name or position from a company name.

### Download and use a KBO bulk ZIP (optional)

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

## Lead Acquisition UI

The local UI provides two workflows: enrich an existing iQualif file or
scrape new companies through a supported source. The first MVP runs the
existing Python pipeline in an RQ background worker, stores job progress in
PostgreSQL, and keeps input/output files in the configured Google Drive-backed
directory.

Start it with Docker Compose:

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:5173`. The API health endpoint is at
`http://localhost:8000/health`. The default input directory is:
`/home/sohaib/GoogleDrive/WorkDrive/Databases/Iqualif`.

The browser upload is stored under `data/uploads/` during local development.
The original uploaded file is never overwritten. Outputs are written under
`data/ui-outputs/<job-id>/`; the database stores job status, stage progress,
row outcomes, and evidence metadata. In production, replace these local
directories with tenant-isolated object storage such as S3.

## Infobel Scraping (reCAPTCHA Auto-Solve)

Scrapes [Infobel Belgium](https://www.infobel.com/fr/belgium/) business details — name, address, TVA, phone, email, hours, financial data — by searching sector + region.

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

## Development Status

The repository foundation is created. The next implementation milestone is a
shared canonical `Lead` schema, profile loading, CSV normalization, and tests.
The existing insurance project remains separate and is not modified by this
repository.
