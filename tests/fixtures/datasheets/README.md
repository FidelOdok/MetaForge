# Datasheet fixtures

Real public datasheets used by the tier-1 UAT scenarios in
`tests/uat/scenarios/tier1/datasheets-real.md`. These fixtures
validate that the MetaForge knowledge base actually answers
engineer questions — not synthetic markers.

## What's here

| File | Role |
|---|---|
| `manifest.yaml` | Source URL + sha256 for each datasheet, paths to the committed extracts |
| `<mpn>.txt` | Extracted text from the datasheet PDF (committed; this is the ingest fixture) |
| `<mpn>.gt.yaml` | Ground-truth queries: `{ id, category, question, expected_substring, expected_section }` |
| `README.md` | This file |

The binary PDFs themselves are **not** in the repo — they're fetched
on demand by `scripts/datasheets/fetch_and_extract.py` into a
gitignored `.cache/datasheets/` directory.

## How a UAT scenario uses these files

For each `## Scenario:` block in `datasheets-real.md`:

1. **Given** — the fixture file at `tests/fixtures/datasheets/<mpn>.txt`
   exists with the sha256 pinned in `manifest.yaml`.
2. **When** — the agent reads the file off disk and calls
   `mcp__metaforge__knowledge_ingest` with the content; then calls
   `mcp__metaforge__knowledge_search` with the engineer's natural-
   language query from `<mpn>.gt.yaml`.
3. **Then** — the top hit's `content` contains the literal
   `expected_substring`, the citation `source_path` is the fixture
   path, and `metadata.mpn` round-trips.

## Adding a datasheet

1. Find a publicly-available manufacturer datasheet PDF.
2. Append an entry to `manifest.yaml` with `pdf_sha256` and
   `text_sha256` empty:
   ```yaml
   - mpn: <PART>
     vendor: <Vendor>
     family: <MCU|Sensor|Power|Comm|Wireless|...>
     source_url: <https://...>
     pdf_sha256: ""
     text_path: tests/fixtures/datasheets/<mpn-lower>.txt
     text_sha256: ""
     license_note: |
       Public datasheet by <Vendor>. Excerpt committed for UAT testing
       under fair-use technical-reference doctrine.
   ```
3. Run `python scripts/datasheets/fetch_and_extract.py`. It will:
   - Download the PDF to `.cache/datasheets/<mpn-lower>.pdf`
   - Compute sha256, write it back to `manifest.yaml`
   - Extract text via `pdfplumber`, write `tests/fixtures/datasheets/<mpn-lower>.txt`
   - Compute and pin `text_sha256`
4. **Manually review** the produced `.txt`. Check that key spec tables
   (electrical, thermal, package) extracted readably. If pdfplumber
   mangled a critical table, document it in `<mpn>.gt.yaml.notes` and
   skip questions that depend on the mangled section.
5. **Author** `<mpn>.gt.yaml` — pick ~10 engineer-realistic queries
   spanning the 10 datasheet value categories
   (`docs/uat/kb-test-plan.md` §11). Each `expected_substring` MUST be
   literally present in the produced `.txt`.
6. Add the corresponding `## Scenario:` blocks to
   `tests/uat/scenarios/tier1/datasheets-real.md` — see existing
   blocks for the template.

## Why text excerpts and not the PDFs?

- **Decoupled from MET-399.** The production server-side PDF parser
  is not yet wired (see `docs/uat/kb-test-plan.md` KB-CLI-003). These
  fixtures intentionally test retrieval against pre-extracted text so
  failures can be attributed to the KB's retrieval / chunking /
  embedding behavior, not to the parser.
- **License hygiene.** Manufacturer PDFs vary in their redistribution
  terms. Committing extracted text excerpts — clearly attributed,
  cited to the original URL — is squarely fair-use technical
  reference. Committing the binary PDF is murkier and gains us
  nothing (the script can refetch any time from the manifest URL).
- **Repo size.** PDFs run 1–10 MB each; their text extracts run
  ~70 KB (compact part) to ~1.3 MB (full reference manuals like
  RP2040). Even at the high end, text is order-of-magnitude smaller
  than the source PDF and stays well under git LFS territory.

## Refresh cadence

Datasheets get revised by manufacturers. The script's sha256 check is
how we notice. When `pdf_sha256` mismatches:

- The script reports the difference and exits non-zero.
- Re-run after reading the manufacturer's revision history.
- Re-extract the text, diff against the old `.txt`, and update
  `<mpn>.gt.yaml.expected_substring` for any spec that materially
  changed (e.g., a new silicon revision dropped Iq from 60 nA to
  40 nA).
- Bump the new `pdf_sha256` and `text_sha256` in `manifest.yaml`.
- Update the verdict columns in `docs/uat/kb-test-plan.md` §11.

## License & attribution

Each `<mpn>.gt.yaml` carries an explicit `license_note` repeating the
attribution. The committed text excerpts are for technical-reference
UAT use only; they are not redistributed in any other form. The
canonical document remains at the manufacturer URL pinned in
`manifest.yaml`.
