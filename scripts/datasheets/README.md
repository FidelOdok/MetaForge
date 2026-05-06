# scripts/datasheets

Fixture-prep tooling for the real-content KB UAT suite. **Not** part
of the production knowledge-ingest path — this script runs locally,
on a developer machine, when datasheet fixtures are added or
refreshed.

## When to run

- **Onboarding** — first time setting up the repo and you want to
  verify the committed `<mpn>.txt` fixtures match the upstream PDFs.
- **Adding a datasheet** — after appending a new entry to
  `tests/fixtures/datasheets/manifest.yaml`.
- **Refreshing** — when a manufacturer revises a datasheet (the
  pinned `pdf_sha256` will mismatch and the script will tell you).

## Usage

```bash
# 1. Install dev deps (one-time per checkout)
pip install -e ".[dev]"

# 2. Fetch + extract everything in the manifest
python scripts/datasheets/fetch_and_extract.py

# 3. Limit to one part (case-insensitive substring on mpn)
python scripts/datasheets/fetch_and_extract.py --only RP2040

# 4. Force re-download (clears the .cache/datasheets/ entry first)
python scripts/datasheets/fetch_and_extract.py --refresh-pdf
```

## What it does

For each entry in `manifest.yaml`:

1. Downloads the PDF from `source_url` to `.cache/datasheets/<mpn>.pdf`
   (gitignored). Skips the download if the file is already cached.
2. Computes the PDF's SHA-256.
   - If `manifest.yaml` already pins a `pdf_sha256`, the script
     compares; on mismatch it exits non-zero (manufacturer revised
     the document upstream).
   - If the pin is empty, the script writes the freshly-computed
     value back into `manifest.yaml`.
3. Extracts text from the PDF using `pdfplumber` (page-by-page, with
   `## Page N` headers) and writes `tests/fixtures/datasheets/<mpn>.txt`.
4. Computes the extracted-text SHA-256 with the same pin/compare
   policy as above.

The script is **idempotent**: a clean run after a successful one
produces no diff to either `manifest.yaml` or any `.txt` file.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (with or without manifest updates) |
| 1 | Manifest file missing |
| 2 | PDF SHA-256 mismatched a pinned value (upstream revision) |
| 3 | pdfplumber extracted no text from a PDF (corrupt download or scanned-only PDF) |
| 4 | Extracted-text SHA-256 mismatched a pinned value (extractor drift) |

## After a successful run

1. **Review** the produced `<mpn>.txt`. Open it next to the original
   PDF and confirm critical spec tables (electrical characteristics,
   thermal, package) are readable. pdfplumber occasionally collapses
   table columns — when it does, note it in `<mpn>.gt.yaml.notes`
   and avoid questions that depend on the mangled section.

2. **Author** `<mpn>.gt.yaml` with ~10 engineer-realistic queries.
   Each `expected_substring` MUST be present in the produced `.txt`
   (verify with `grep -F "<expected_substring>" <mpn>.txt`).

3. **Add scenarios** to `tests/uat/scenarios/tier1/datasheets-real.md`,
   one `## Scenario:` block per gt.yaml query. The block uses the
   query id as its title and references the fixture path in the
   `Given` section.

4. **Update** `docs/uat/kb-test-plan.md` §11 with the new catalog
   rows and the freshly-baselined verdict columns from the first
   `/uat-cycle12 --tier 1 --only "KB-DS-"` run.

## Network access

The script needs HTTPS access to the manufacturer URLs in
`manifest.yaml`. Currently:

- `datasheets.raspberrypi.com`
- `www.bosch-sensortec.com`
- `www.ti.com`

If you're running in a sandboxed environment, allow these hosts or
run the script outside the sandbox. The downloaded PDFs land in
`.cache/datasheets/`; once cached, the script does not touch the
network on subsequent runs.
