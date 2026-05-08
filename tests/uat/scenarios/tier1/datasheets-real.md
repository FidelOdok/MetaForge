# Tier-1 — real-datasheet retrieval QA (KB-DS)

Validates: MET-346 (ingest), MET-293 (search top_k), MET-335 (citations).
Tier: 1
Run: `/uat-cycle12 --tier 1 --only "KB-DS-"`

These scenarios exercise the MetaForge knowledge base against
**real public datasheets** with **engineer-style natural-language
queries** and **literal ground-truth substrings** drawn directly
from the source PDF. They replace synthetic-marker testing for the
component-domain corpus.

Fixture inputs:
- `tests/fixtures/datasheets/<mpn>.txt` — extracted-text fixture
- `tests/fixtures/datasheets/<mpn>.gt.yaml` — ground-truth queries
- `tests/fixtures/datasheets/manifest.yaml` — sha256 pins

If a fixture is missing or its sha256 disagrees with `manifest.yaml`,
the agent reports BLOCKED — not FAIL — for every scenario in that
file group. To prepare fixtures, see
`scripts/datasheets/README.md`.

Scenarios are generated from the gt.yaml files by
`scripts/datasheets/generate_scenarios.py`. Edit the gt.yaml files
and re-run the generator; do not hand-edit this file.

If top-1 fails the substring assertion but the substring is present
in top-2 or top-3, mark the scenario FAIL and capture the chunk
contents in the report — that is a retrieval-ranking signal, not a
test-harness regression.

---

## RP2040 — Raspberry Pi (MCU)

Fixture: `tests/fixtures/datasheets/rp2040.txt`. 10 queries.

---

## Scenario: KB-DS-RP2040-PWR-001 — RP2040 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Pin Descriptions / Power Supplies`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the nominal core supply voltage (DVDD) of the RP2040?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"1.1V"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PWR-002 — RP2040 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Pin Descriptions / Power Supplies`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What input range is supported by the RP2040 internal voltage regulator (VREG_VIN)?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"1.8V to 3.3V"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PERF-001 — RP2040 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `About / Architecture`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum CPU clock frequency of the RP2040?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"133MHz"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PERF-002 — RP2040 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How many CPU cores does the RP2040 have, and which architecture?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"Dual ARM Cortex-M0+"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PERF-003 — RP2040 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Features / ADC`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What ADC does the RP2040 include?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"12-bit conversion"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PERF-004 — RP2040 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Features / PIO`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How many PIO state machines does the RP2040 have?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"8 PIO state machines"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PERF-005 — RP2040 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How many GPIO pins are available on the RP2040?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"30 GPIO pins"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-MEM-001 — RP2040 memory
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Features / Memory`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How much on-chip SRAM does the RP2040 have?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"264kB"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-PKG-001 — RP2040 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Pinout / Package`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What package is the RP2040 supplied in?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"QFN-56"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-RP2040-THERM-001 — RP2040 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/rp2040.txt` is present and its
  sha256 matches the pin for `RP2040` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://rp2040`.
- Expected citation section (soft assertion): `Errata / Document History`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://rp2040`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Raspberry Pi", "mpn": "RP2040" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What minimum operating temperature is the RP2040 qualified for?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://rp2040"`.
- Top-1 hit's `content` contains the literal substring `"qualified to -40°C"`.
- Top-1 hit's `metadata.mpn == "RP2040"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## BME280 — Bosch Sensortec (Sensor (T/P/H))

Fixture: `tests/fixtures/datasheets/bme280.txt`. 10 queries.

---

## Scenario: KB-DS-BME280-PWR-001 — BME280 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the supply voltage range VDD for the BME280?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"1.71 V to 3.6 V"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-PWR-002 — BME280 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What VDDIO range does the BME280 support for its digital interface?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"1.2 V to 3.6 V"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-PWR-003 — BME280 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features / Power Consumption`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the BME280's current consumption in sleep mode?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"0.1 µA"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-PERF-001 — BME280 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features / Digital Interface`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum I²C clock speed supported by the BME280?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"3.4 MHz"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-PERF-002 — BME280 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features / Digital Interface`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum SPI clock speed supported by the BME280?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"10 MHz"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-PERF-003 — BME280 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `General Description`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Does the BME280 support both SPI and I²C interfaces?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"SPI and I²C"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-THERM-001 — BME280 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the operating temperature range of the BME280?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"Operating range -40"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-REL-001 — BME280 reliability
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Electrical Characteristics`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the ESD HBM rating of the BME280?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"±2 kV"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-PKG-001 — BME280 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What are the package dimensions of the BME280?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"2.5 mm x 2.5 mm x 0.93 mm"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-BME280-CMP-001 — BME280 compliance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/bme280.txt` is present and its
  sha256 matches the pin for `BME280` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://bme280`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://bme280`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Bosch Sensortec", "mpn": "BME280" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Is the BME280 RoHS-compliant and halogen-free?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://bme280"`.
- Top-1 hit's `content` contains the literal substring `"RoHS compliant, halogen-free, MSL1"`.
- Top-1 hit's `metadata.mpn == "BME280"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## TPS62840 — Texas Instruments (Power (low-Iq buck))

Fixture: `tests/fixtures/datasheets/tps62840.txt`. 10 queries.

---

## Scenario: KB-DS-TPS62840-PWR-001 — TPS62840 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Description / Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the typical operating quiescent current of the TPS62840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"60 nA"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-PWR-002 — TPS62840 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Description`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the input voltage range of the TPS62840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"1.8 V to 6.5 V"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-PWR-003 — TPS62840 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Description`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum output current the TPS62840 can deliver?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"750 mA"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-PERF-001 — TPS62840 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Detailed Description`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the nominal switching frequency of the TPS62840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"1.8 MHz"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-THERM-001 — TPS62840 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Recommended Operating Conditions`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum operating junction temperature TJ for the TPS62840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"125 °C"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-REL-001 — TPS62840 reliability
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `ESD Ratings`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the ESD HBM rating of the TPS62840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"±2000"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-REL-002 — TPS62840 reliability
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `ESD Ratings`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the ESD CDM rating of the TPS62840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"±500"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-PKG-001 — TPS62840 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Device Information / Mechanical`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Is the TPS62840 available in a SON-8 package?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"SON-8"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-APP-001 — TPS62840 application
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Description`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What control architecture does the TPS62840 use?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"DCS-Control"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-TPS62840-CMP-001 — TPS62840 compliance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/tps62840.txt` is present and its
  sha256 matches the pin for `TPS62840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://tps62840`.
- Expected citation section (soft assertion): `Mechanical, Packaging, and Orderable Information`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://tps62840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "TPS62840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Are TPS62840 orderable parts RoHS compliant?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://tps62840"`.
- Top-1 hit's `content` contains the literal substring `"RoHS"`.
- Top-1 hit's `metadata.mpn == "TPS62840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## STM32H723VGT6 — STMicroelectronics (MCU (flagship))

Fixture: `tests/fixtures/datasheets/stm32h723vgt6.txt`. 10 queries.

---

## Scenario: KB-DS-STM32H723VGT6-PWR-001 — STM32H723VGT6 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features / Clock, reset, and supply management`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the supply voltage range of the STM32H723VGT6?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"1.62 V to 3.6 V"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-PERF-001 — STM32H723VGT6 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features / Core`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum CPU clock frequency of the STM32H723VGT6?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"550 MHz"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-PERF-002 — STM32H723VGT6 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features / Core`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How much L1 cache is available on the STM32H723VGT6 Cortex-M7 core?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"32-Kbyte data cache and 32-Kbyte"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-MEM-001 — STM32H723VGT6 memory
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features / Memories`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How much embedded flash memory does the STM32H723VGT6 contain?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"1 Mbyte of embedded flash"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-SIG-001 — STM32H723VGT6 signal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Pinouts and pin descriptions / Legend`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Does the STM32H723VGT6 have 5 V tolerant I/Os?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"FT 5 V tolerant I/O"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-PKG-001 — STM32H723VGT6 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features / Packages`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the LQFP100 package body size for the STM32H723VGT6?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"(14x14 mm)"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-THERM-001 — STM32H723VGT6 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Description`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the ambient operating temperature range of the STM32H723VGT6?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"–40 to +85 °C"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-APP-001 — STM32H723VGT6 application
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features / Debug mode`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which debug interfaces does the STM32H723VGT6 support?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"SWD and JTAG interfaces"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-CMP-001 — STM32H723VGT6 compliance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Are STM32H723VGT6 packages RoHS-compliant?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"ECOPACK2 compliant"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-STM32H723VGT6-ERR-001 — STM32H723VGT6 errata
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/stm32h723vgt6.txt` is present and its
  sha256 matches the pin for `STM32H723VGT6` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://stm32h723vgt6`.
- Expected citation section (soft assertion): `Introduction`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://stm32h723vgt6`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "STMicroelectronics", "mpn": "STM32H723VGT6" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Where is the errata sheet for the STM32H723 documented?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://stm32h723vgt6"`.
- Top-1 hit's `content` contains the literal substring `"ES0491"`.
- Top-1 hit's `metadata.mpn == "STM32H723VGT6"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## ESP32-WROOM-32 — Espressif (Wireless module)

Fixture: `tests/fixtures/datasheets/esp32-wroom-32.txt`. 10 queries.

---

## Scenario: KB-DS-ESP32-WROOM-32-PWR-001 — ESP32-WROOM-32 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Operating Conditions`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What supply voltage range does the ESP32-WROOM-32 require?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"Operatingvoltage/Powersupply: 3.0~3.6V"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-PERF-001 — ESP32-WROOM-32 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / CPU and On-Chip Memory`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum CPU clock frequency of the ESP32-WROOM-32?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"32-bitLX6microprocessor,upto240MHz"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-PERF-002 — ESP32-WROOM-32 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Wi-Fi`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which Wi-Fi standards does the ESP32-WROOM-32 support?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"802.11b/g/n"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-PERF-003 — ESP32-WROOM-32 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Bluetooth`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which Bluetooth specification does the ESP32-WROOM-32 implement?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"BluetoothV4.2BR/EDRandBluetoothLE"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-MEM-001 — ESP32-WROOM-32 memory
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Integrated Components on Module`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How much SPI flash is integrated on the ESP32-WROOM-32?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"4MBSPIflash"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-PKG-001 — ESP32-WROOM-32 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Module Overview / Ordering Information`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What are the physical dimensions of the ESP32-WROOM-32 module?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"18×25.5×3.1"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-THERM-001 — ESP32-WROOM-32 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Operating Conditions`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the operating ambient temperature range of the ESP32-WROOM-32?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"Operatingambienttemperature: –40~85°C"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-REL-001 — ESP32-WROOM-32 reliability
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Product Handling / Electrostatic Discharge (ESD)`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the human body model ESD rating of the ESP32-WROOM-32?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"Humanbodymodel(HBM):±2000V"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-APP-001 — ESP32-WROOM-32 application
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Antenna Options`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What antenna option ships on the ESP32-WROOM-32?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"On-boardPCBantenna"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-ESP32-WROOM-32-CMP-001 — ESP32-WROOM-32 compliance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/esp32-wroom-32.txt` is present and its
  sha256 matches the pin for `ESP32-WROOM-32` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://esp32-wroom-32`.
- Expected citation section (soft assertion): `Features / Certification`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://esp32-wroom-32`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Espressif", "mpn": "ESP32-WROOM-32" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which environmental certifications does the ESP32-WROOM-32 carry?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://esp32-wroom-32"`.
- Top-1 hit's `content` contains the literal substring `"REACH/RoHS"`.
- Top-1 hit's `metadata.mpn == "ESP32-WROOM-32"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## nRF52840 — Nordic Semiconductor (BLE SoC)

Fixture: `tests/fixtures/datasheets/nrf52840.txt`. 10 queries.

---

## Scenario: KB-DS-NRF52840-PWR-001 — nRF52840 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list / Flexible power management`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the supply voltage range of the nRF52840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"1.7 V to 5.5 V supply voltage range"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-PWR-002 — nRF52840 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list / Flexible power management`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the System OFF mode current of the nRF52840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"0.4 µA at 3 V in System OFF mode"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-PERF-001 — nRF52840 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which CPU core does the nRF52840 use and at what frequency?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"ARM ® Cortex ® -M4 32-bit processor with FPU, 64 MHz"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-PERF-002 — nRF52840 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Does the nRF52840 support IEEE 802.15.4 (Thread/Zigbee)?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"IEEE 802.15.4-2006"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-PERF-003 — nRF52840 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list / Advanced on-chip interfaces`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Does the nRF52840 expose a USB controller?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"USB 2.0 full speed"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-MEM-001 — nRF52840 memory
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How much flash and RAM does the nRF52840 have?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"1 MB flash and 256 kB RAM"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-PKG-001 — nRF52840 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list / Package variants`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the body size of the nRF52840 aQFN73 package?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"aQFN 73 package, 7 x 7 mm"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-THERM-001 — nRF52840 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Recommended operating conditions`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the recommended operating temperature range of the nRF52840?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"TA Operating temperature -40 25 85 °C"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-REL-001 — nRF52840 reliability
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Absolute maximum ratings / Environmental aQFN73 package`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the HBM ESD rating of the nRF52840 in the aQFN73 package?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"ESD HBM Human Body Model 2 kV"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-NRF52840-APP-001 — nRF52840 application
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/nrf52840.txt` is present and its
  sha256 matches the pin for `nRF52840` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://nrf52840`.
- Expected citation section (soft assertion): `Feature list / Advanced on-chip interfaces`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://nrf52840`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Nordic Semiconductor", "mpn": "nRF52840" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Does the nRF52840 include an NFC tag interface?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://nrf52840"`.
- Top-1 hit's `content` contains the literal substring `"Type 2 near field communication"`.
- Top-1 hit's `metadata.mpn == "nRF52840"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## LM2596 — Texas Instruments (Buck regulator)

Fixture: `tests/fixtures/datasheets/lm2596.txt`. 10 queries.

---

## Scenario: KB-DS-LM2596-PWR-001 — LM2596 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum input voltage of the LM2596?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"Input voltage range up to 40 V"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-PWR-002 — LM2596 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum output load current the LM2596 can drive?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"3-A output load current"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-PWR-003 — LM2596 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the standby (quiescent) current of the LM2596 in shutdown?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"80 μA"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-PERF-001 — LM2596 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the switching frequency of the LM2596?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"150-kHz fixed-frequency internal oscillator"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-PERF-002 — LM2596 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the adjustable output voltage range of the LM2596-ADJ?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"37-V ±4%"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-PKG-001 — LM2596 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which packages is the LM2596 available in?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"Available in TO-220 and TO-263 packages"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-THERM-001 — LM2596 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Specifications / Absolute Maximum Ratings`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum junction temperature for the LM2596?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"Maximum junction temperature 150 °C"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-THERM-002 — LM2596 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Specifications / Absolute Maximum Ratings`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the storage temperature range of the LM2596?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"Storage temperature, T –65 150 °C"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-REL-001 — LM2596 reliability
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Specifications / ESD Ratings`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the HBM ESD rating of the LM2596?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"Human-body model (HBM)"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-LM2596-APP-001 — LM2596 application
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/lm2596.txt` is present and its
  sha256 matches the pin for `LM2596` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://lm2596`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://lm2596`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Texas Instruments", "mpn": "LM2596" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Does the LM2596 include thermal shutdown and current-limit protection?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://lm2596"`.
- Top-1 hit's `content` contains the literal substring `"Thermal shutdown and current-limit protection"`.
- Top-1 hit's `metadata.mpn == "LM2596"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

## MCP2515 — Microchip (CAN controller (AEC-Q100))

Fixture: `tests/fixtures/datasheets/mcp2515.txt`. 10 queries.

---

## Scenario: KB-DS-MCP2515-PWR-001 — MCP2515 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features / Low-Power CMOS Technology`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the supply voltage range of the MCP2515?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"Operates from 2.7V-5.5V"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-PWR-002 — MCP2515 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features / Low-Power CMOS Technology`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the typical active supply current of the MCP2515?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"5 mA active current (typical)"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-PWR-003 — MCP2515 power
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features / Low-Power CMOS Technology`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the typical sleep-mode standby current of the MCP2515?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"1 μA standby current (typical) (Sleep mode)"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-PERF-001 — MCP2515 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Which CAN protocol version does the MCP2515 implement and at what bit rate?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"CAN V2.0B at 1 Mb/s"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-PERF-002 — MCP2515 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the maximum SPI clock frequency of the MCP2515?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"High-Speed SPI Interface (10 MHz)"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-PERF-003 — MCP2515 performance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features / Receive Buffers, Masks and Filters`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `How many CAN acceptance filters does the MCP2515 provide?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"Six 29-bit filters"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-THERM-001 — MCP2515 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features / Temperature Ranges Supported`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the industrial-grade ambient temperature range of the MCP2515?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"Industrial (I): -40°C to +85°C"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-THERM-002 — MCP2515 thermal
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Features / Temperature Ranges Supported`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `What is the extended ambient temperature range of the MCP2515?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"Extended (E): -40°C to +125°C"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-PKG-001 — MCP2515 package
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Package Types`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `In which packages is the MCP2515 supplied?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"18-Lead PDIP/SOIC"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Scenario: KB-DS-MCP2515-CMP-001 — MCP2515 compliance
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/mcp2515.txt` is present and its
  sha256 matches the pin for `MCP2515` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://mcp2515`.
- Expected citation section (soft assertion): `Packaging Information`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://mcp2515`
   - `knowledge_type`: `component`
   - `metadata`: `{ "vendor": "Microchip", "mpn": "MCP2515" }`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `Is the MCP2515 lead-free / RoHS compliant?`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://mcp2515"`.
- Top-1 hit's `content` contains the literal substring `"Pb-free"`.
- Top-1 hit's `metadata.mpn == "MCP2515"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).

---

## Acceptance

- All 80 scenarios run in a single `/uat-cycle12 --tier 1 --only "KB-DS-"` invocation.
- Baseline target on first run: ≥ 53 / 80 PASS. Failures are diagnostic
  signal, not test-harness regressions — capture the top-3 chunk
  contents in the report so the failure mode (retrieval ranking,
  extraction quality, chunk boundary) is immediately diagnosable.
- Verdict roll-up updates `docs/uat/kb-test-plan.md` §11.
