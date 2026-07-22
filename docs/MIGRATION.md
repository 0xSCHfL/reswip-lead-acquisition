# Migration from Insurance Project

This document describes which logic was reused from the insurance project
(`/home/sohaib/Work/projects/Reswip-Insurance-Partner`) and which was
deliberately excluded.

## Reusable Logic Extracted

### TVA Normalization
- **Source**: `scripts/utility/iqualif_lookup.py:normalize_vat()`
- **Reuse**: Core algorithm — strip non-alphanumeric, uppercase, prepend `BE`
- **Location**: `src/reswip_leads/core/models.py:normalize_tva()`
- **Changes**: Added `None` handling, simplified prefix logic

### iQualif CSV Import
- **Source**: `scripts/utility/iqualif_lookup.py` + `scripts/iqualif/enrich.py`
- **Reuse**: CSV sniffing (delimiter detection), encoding fallback (utf-8-sig → latin-1 → cp1252), VAT-indexed lookup, field aliasing (multiple column name variants)
- **Location**: `src/reswip_leads/sources/iqualif/importer.py`
- **Changes**: Removed insurance-specific phone validation, removed area code matching, sector-neutral field extraction

### KBO ZIP Reading
- **Source**: `scripts/kbo-zip/verify.py`
- **Reuse**: ZIP CSV parsing, enterprise/denomination/address/contact/activity extraction, preferred denomination selection (language priority), preferred address selection (type priority)
- **Location**: `src/reswip_leads/verification/kbo/zip_reader.py`
- **Changes**: Removed insurance-specific NACE 66220 default, removed broker state classification, made activity code configurable

### Pappers Scraping
- **Source**: `scripts/pappers/enrich.py` + `scripts/pappers/find_emails.py`
- **Reuse**: Slug-based URL construction, director name extraction from search-officers links, email extraction (Cloudflare email protection decoding, regex patterns), phone extraction
- **Location**: `src/reswip_leads/enrichment/pappers.py`
- **Changes**: Removed proxy rotation (optional add-on), removed parallel workers (orchestration concern), removed insurance-specific email validation

### KBO Web Scraping
- **Source**: `scripts/kbo-web/enrich.py`
- **Reuse**: Company page fetching, director extraction, contact field filling (merge-if-empty pattern)
- **Location**: `src/reswip_leads/enrichment/kbo_web.py`
- **Changes**: Removed proxy support, removed website scraping fallback, sector-neutral

### Belgian Province/Region Classification
- **Source**: `scripts/kbo-zip/enrich.py` (postcode lookup)
- **Reuse**: Province-to-region mapping, province-to-language mapping
- **Location**: `src/reswip_leads/core/fields.py`
- **Changes**: Complete rewrite with canonical province names, case-insensitive lookup

## Deliberately Excluded

### Insurance-Specific Logic
| Item | Reason |
|------|--------|
| FSMA data loading | Insurance regulator — not applicable to energy |
| NACE code 66220 default | Insurance broker activity code |
| `is_valid_broker_email()` | Insurance-specific email filtering |
| Broker state classification | Insurance-specific (ACTIVE_BROKER, etc.) |
| `scrape_website_contacts()` | Tightly coupled to insurance broker patterns |
| SearXNG integration | Optional enrichment, not core |

### Infrastructure/Operations
| Item | Reason |
|------|--------|
| Proxy rotation (`ProxyRotator`) | Infrastructure concern, not business logic |
| Parallel workers (`ThreadPoolExecutor`) | Orchestration, not core logic |
| Checkpoint/resume system | Implementation detail |
| Logging setup | Application configuration |
| CLI argument parsing | Script-level concern |

## Field Mapping

| Insurance CSV | Lead Model | Notes |
|---------------|------------|-------|
| `Company Name` | `company_name` | Required |
| `VAT Number` | `tva` | Primary key, auto-normalized |
| `Address` | `address` | Optional |
| `City` | `city` | Optional |
| `Province` | `province` | Classified automatically |
| `Region` | `region` | Classified from province |
| `First Name` | `first_name` | Optional — never invented |
| `Last Name` | `last_name` | Optional — never invented |
| `Position` | `position` | Optional — never invented |
| `Email Address` | `email` | Optional |
| `Office Phone` | `phone` | Optional |
| `Mobile Phone` | `mobile` | Optional |
| `Website` | `website` | Optional |
| `Source` | `source` | Tracks data provenance |

## Next Steps

1. Implement actual HTTP calls in Pappers and KBO web enrichers
2. Add deduplication module (TVA-based)
3. Add CSV/XLSX export for Zoho CRM
4. Add energy profile NACE codes
5. Wire up full pipeline orchestration
