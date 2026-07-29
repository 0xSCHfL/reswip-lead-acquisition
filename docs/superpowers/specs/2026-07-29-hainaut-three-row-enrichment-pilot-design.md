# Hainaut Three-Row Enrichment Pilot

## Goal

Validate the end-to-end enrichment behavior on three real Hainaut iQualif
records before running the full 1,000-row database.

## Source

`/home/sohaib/GoogleDrive/gdrive/Databases/Energie/Belgium/Wallonie/Hainaut/iQUALIF-1000-No-Groups/hainaut_iqualif_1000_no_groups (Copy).csv`

The source is semicolon-delimited UTF-8 with BOM and contains 1,000 data rows.
The original file must remain unchanged.

## Pilot records

Use three valid-TVA rows selected from the source, including:

- a record with missing email and website;
- a second record with missing email and website;
- a record with existing contact data to verify the no-overwrite rule.

The selected records and their exact source values must be copied into a
separate pilot input CSV. The pilot output must be written separately from the
source and existing operational outputs.

## Intended enrichment order

1. Import and normalize iQualif fields, including the TVA.
2. Run Pappers first to discover first name, last name, and position.
3. Run KBO web to fill missing fields and provide official company evidence.
4. Store company status from KBO as the authoritative status.
5. Run Infobel by TVA only for leads that still have missing contact fields.
6. Preserve all non-empty values already present in the input or returned by an
   earlier source.
7. Export the pilot result with source/evidence information where supported.

Infobel is a secondary verification and contact-enrichment source. It must not
override KBO company status or overwrite existing evidence-backed fields.

## Acceptance criteria

- Exactly three pilot leads are imported and exported.
- Pappers is invoked before KBO web.
- KBO status is retained separately from general contact fields.
- Infobel receives normalized TVA values and is invoked only for incomplete
  contact data.
- Existing input values are not overwritten.
- Missing values remain blank when no reliable source provides evidence.
- Each pilot row can be manually reviewed for company identity, contact fields,
  position, status, and source evidence.
- Automated tests cover ordering, fallback eligibility, status handling, and
  no-overwrite behavior before the full database is attempted.

## Out of scope

- Processing the full 1,000-row database.
- Publishing or importing the pilot output into Zoho.
- Treating Pappers or Infobel as authoritative for legal company status.
- Unverified contact-name invention.
