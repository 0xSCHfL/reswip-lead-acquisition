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

## Development Status

The repository foundation is created. The next implementation milestone is a
shared canonical `Lead` schema, profile loading, CSV normalization, and tests.
The existing insurance project remains separate and is not modified by this
repository.
