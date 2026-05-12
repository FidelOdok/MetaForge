# Datasheet ingestion

How to add a manufacturer datasheet to the Digital Twin's knowledge
layer. Covers the end-to-end developer flow: download → parse →
ingest → query.

> **Reference:** the model lives in `twin_core.models.Datasheet`; the
> parser in `digital_twin.datasheets.parser`. See MET-430 (pipeline)
> and MET-431 (ESP32 proof point).

## The pipeline at a glance

```
manufacturer PDF
       │
       │  digital_twin.datasheets.parse_datasheet_pdf()
       ▼
twin_core.models.Datasheet  (mpn, revision, file_hash, page_count, …)
       │
       │  TwinAPI.ingest_datasheet()
       ▼
Datasheet node in the Twin
   ├─ SUPERSEDES → previous revision (if one exists for this MPN)
   └─ DESCRIBES  → every Component that already exists for this MPN
```

## Adding a new datasheet

The repo treats datasheets as **fetched-on-demand** content. Only the
extracted `.txt` excerpt is committed; the source PDF is downloaded
locally to `.cache/datasheets/` and gitignored.

### 1. Append to the manifest

`tests/fixtures/datasheets/manifest.yaml`:

```yaml
- mpn: ESP32-WROOM-32E
  vendor: Espressif
  family: MCU (Wi-Fi/Bluetooth SoC)
  source_url: https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.pdf
  pdf_sha256: ''                # populated on first fetch
  text_path: tests/fixtures/datasheets/esp32-wroom-32e.txt
  text_sha256: ''               # populated on first fetch
  license_note: '...'
```

### 2. Fetch + extract

```bash
pip install -e ".[dev]"
python scripts/datasheets/fetch_and_extract.py --only ESP32
```

This downloads the PDF, computes `pdf_sha256`, writes the extracted
`.txt` excerpt, and computes `text_sha256`. Commit the manifest update
and the `.txt` file.

### 3. Ingest into the Twin (Python)

```python
from digital_twin.datasheets import parse_datasheet_pdf
from twin_core.api import InMemoryTwinAPI

twin = await InMemoryTwinAPI.create_from_env()

ds = parse_datasheet_pdf(
    ".cache/datasheets/ESP32-WROOM-32E.pdf",
    mpn="ESP32-WROOM-32E",
    manufacturer="Espressif",
    revision="rev3",
    source_url="https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32e_datasheet_en.pdf",
)
await twin.ingest_datasheet(ds)
```

### 4. Verify

```python
current = await twin.get_current_datasheet("ESP32-WROOM-32E")
assert current is not None
assert current.revision == "rev3"

history = await twin.find_datasheets_by_mpn("ESP32-WROOM-32E")
print([d.revision for d in history])  # all revisions, in insertion order
```

## Versioning

`ingest_datasheet()` is **idempotent on `file_hash`**: re-ingesting
the same PDF bytes returns the existing node unchanged.

When a different `file_hash` lands for an MPN that already has a
datasheet, the new node is added and a `SUPERSEDES` edge is created
pointing from the new revision to the previous current. The supersedes
chain is the source of truth for "which datasheet is current".

`get_current_datasheet(mpn)` returns the head of the chain — the
datasheet with no incoming `SUPERSEDES` edge.

## Component linking

If a `Component` node already exists with `part_number == datasheet.mpn`,
ingest auto-creates a `DESCRIBES` edge from the Datasheet to the
Component. The reverse — auto-creating a Component when none exists —
is **intentionally not done** to avoid silently injecting nodes the
user didn't author. The supply chain agent (or manual `add_component`)
populates Components; the datasheet then connects on the next ingest.

## What's still ahead (MET-430 follow-ups)

The current pipeline ships:

- [x] PDF → page-segmented text (via `extract_pages`)
- [x] Idempotent ingest with file_hash
- [x] Supersedes chain across revisions
- [x] Auto-link to existing Component

Still queued under MET-430:

- [ ] Table detection → structured rows (`extract_tables`)
- [ ] Typed property extraction with confidence (feeds `knowledge.extract`)
- [ ] `forge knowledge ingest-datasheet` CLI
- [ ] Staleness detection (`published_at > last_extraction_at`)
- [ ] `knowledge.search` defaults to current revision

## See also

- [knowledge-ingestion-playbook.md](../architecture/knowledge-ingestion-playbook.md) — general knowledge ingestion patterns
- [`scripts/datasheets/README.md`](../../scripts/datasheets/README.md) — fixture tooling
- `tests/integration/test_esp32_datasheet_ingest.py` — end-to-end test
