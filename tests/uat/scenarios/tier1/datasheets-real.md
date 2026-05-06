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

---

## Acceptance

- All 30 scenarios run in a single `/uat-cycle12 --tier 1 --only "KB-DS-"` invocation.
- Baseline target on first run: ≥ 20 / 30 PASS. Failures are diagnostic
  signal, not test-harness regressions — capture the top-3 chunk
  contents in the report so the failure mode (retrieval ranking,
  extraction quality, chunk boundary) is immediately diagnosable.
- Verdict roll-up updates `docs/uat/kb-test-plan.md` §11.
