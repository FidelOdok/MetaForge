---
layout: default
title: UAT Scenario — IoT Sensor Board Design
description: End-to-end user acceptance test for L1-L2 knowledge base maturity
nav_order: 40
---

# UAT Scenario: IoT Sensor Board Design
{: .no_toc }

Complete user acceptance test case for MetaForge v0.1-v0.2. Assumes engineer designing a wireless temperature/humidity sensor board.
{: .fs-6 .fw-300 }

## Table of Contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

## Scenario Setup

**Engineer**: Alice, firmware engineer at IoT startup  
**Task**: Design a battery-powered WiFi temperature/humidity sensor  
**Timeline**: 2 hours (realistic design sprint)  
**Tools**: MetaForge dashboard, CLI, Claude Code IDE plugin  
**Acceptance**: Ship functioning sensor firmware with validated component specs

---

## Phase 1: Project Setup (10 min)

### Step 1a: Create Project via Dashboard

**Actor**: Alice (engineer)

**Actions**:
1. Open MetaForge dashboard: `http://localhost:5173/projects`
2. Click **"New Project"**
3. Fill form:
   - **Name**: `WiFi_Sensor_v1`
   - **Description**: Battery-powered WiFi temp/humidity sensor
   - **Category**: IoT / Firmware
4. Click **Create**

**Expected Results**:
- ✅ Project created with unique `project_id`
- ✅ Empty BOM, empty constraint list
- ✅ Dashboard shows project dashboard (0 components, 0 constraints)
- ✅ Git branch auto-created: `project/wifi-sensor-v1`

### Step 1b: Ingest Reference Datasheets

**Actions**:
```bash
# Terminal 1: Ingest datasheets for key components
forge ingest tests/fixtures/datasheets/ESP32-WROOM-32E_v1.2.pdf --project wifi-sensor-v1
forge ingest tests/fixtures/datasheets/DHT22_v2.0.pdf --project wifi-sensor-v1
forge ingest tests/fixtures/datasheets/BQ27441_v1.1.pdf --project wifi-sensor-v1  # Battery gauge
forge ingest tests/fixtures/datasheets/AMS1117_v3.0.pdf --project wifi-sensor-v1  # LDO regulator

# Verify ingest
forge sources list --project wifi-sensor-v1
```

**Expected Results**:
- ✅ 4 datasheets ingested successfully
- ✅ Each shows: `source_path`, `fragment_count`, `indexed_at`
- ✅ Total chunks indexed: 150–200 (varies by PDF size)
- ✅ Dashboard → Knowledge Sources shows 4 sources

---

## Phase 2: L1 Search (Passive RAG) — 25 min

### Step 2a: Search for WiFi MCU Specs

**Actor**: Alice

**Query**: "What is the operating voltage and maximum current draw of ESP32?"

**Action via Dashboard**:
1. Navigate to **Knowledge > Search**
2. Enter query: `ESP32 operating voltage maximum current draw specifications`
3. Set filters: `knowledge_type: datasheet`, `limit: 10`
4. Click **Search**

**Expected Results**:
```json
[
  {
    "similarity_score": 0.94,
    "source_path": "datasheets/ESP32-WROOM-32E_v1.2.pdf",
    "heading": "Electrical Specifications",
    "chunk_index": 3,
    "content": "The ESP32-WROOM-32E module features...",
    "metadata": {
      "pdf_revision": "v1.2",
      "chip_part_number": "ESP32-WROOM-32E"
    }
  },
  {
    "similarity_score": 0.87,
    "source_path": "datasheets/ESP32-WROOM-32E_v1.2.pdf",
    "heading": "Power Supply",
    "chunk_index": 12,
    "content": "Supply voltage range: 2.5V to 3.6V. Maximum current draw..."
  }
]
```

**Acceptance Criteria**:
- ✅ Returns ≥2 relevant results
- ✅ Similarity scores > 0.85 (not fuzzy matches)
- ✅ Citations include source file, heading, page number
- ✅ Response time < 200ms p95

### Step 2b: Follow Citation Chain

**Action**:
1. Click result #2 (Power Supply chunk)
2. Dashboard shows: full chunk text + highlighted section in PDF preview
3. Click **"View PDF"** → opens ESP32 datasheet to section

**Expected Results**:
- ✅ PDF opens in new tab, scrolled to correct section
- ✅ Highlighting shows exact chunk that was cited
- ✅ Engineer can verify source accuracy

### Step 2c: Search for Sensor Specs

**Query**: "DHT22 temperature humidity sensor operating range supply voltage"

**Expected Results**:
```
similarity_score: 0.91 → "Electrical Characteristics" section
  "Operating voltage: 3.3V to 5.5V"
  "Temperature range: -40°C to +80°C"
  "Humidity range: 0% to 100% RH"
```

**Acceptance Criteria**:
- ✅ Finds DHT22 specs within 2 search results
- ✅ Covers: voltage, temperature, humidity ranges
- ✅ Response time < 200ms

### Step 2d: Search for Power Management

**Query**: "Battery gauge IC BQ27441 fuel gauge I2C communication protocol"

**Expected Results**:
- ✅ Finds BQ27441 datasheet
- ✅ Returns: I2C interface section, voltage range, typical power consumption
- ✅ Similarity > 0.85

---

## Phase 3: L2 Extraction (Confidence Scoring) — 45 min

### Step 3a: Extract Component Properties (L2)

**Requirement**: L2 (knowledge.extract) must be implemented and deployed to dev server.

**Action via CLI**:
```bash
forge extract ESP32-WROOM-32E \
  --properties supply_voltage_v max_current_ma operating_temperature_range_c \
  --project wifi-sensor-v1 \
  --confidence-threshold 0.7
```

**Expected Output**:
```json
{
  "component_identifier": "ESP32-WROOM-32E",
  "properties": [
    {
      "name": "supply_voltage_v",
      "value": 3.3,
      "unit": "V",
      "confidence": 1.0,
      "confidence_reason": "Verbatim from Table 3-2, Electrical Characteristics",
      "source_chunk_id": "chunk-uuid-1",
      "citation": "ESP32-WROOM-32E_v1.2.pdf, p14, Table 3-2",
      "conditions": {"temperature_c": 25}
    },
    {
      "name": "max_current_ma",
      "value": 80,
      "unit": "mA",
      "confidence": 0.9,
      "confidence_reason": "Inferred from 'Recommended DC supply current: 80mA @ 80MHz'",
      "source_chunk_id": "chunk-uuid-2",
      "citation": "ESP32-WROOM-32E_v1.2.pdf, p15, Power Supply",
      "conditions": {"frequency_mhz": 80, "all_io_active": true}
    },
    {
      "name": "operating_temperature_range_c",
      "value": [-40, 85],
      "unit": "°C",
      "confidence": 1.0,
      "confidence_reason": "Verbatim from Storage Temperature Range",
      "source_chunk_id": "chunk-uuid-3",
      "citation": "ESP32-WROOM-32E_v1.2.pdf, p16, Environmental Conditions"
    }
  ],
  "extraction_timestamp": "2026-05-18T14:32:00Z",
  "model_used": "claude-opus",
  "total_source_chunks_searched": 42
}
```

**Acceptance Criteria**:
- ✅ Extracts all 3 properties
- ✅ Confidence scores: 1.0 (verbatim), 0.9 (inferred), ranges covered
- ✅ Each property has citation + source_chunk_id
- ✅ Conditions documented (temp, frequency, etc.)
- ✅ Response time < 2s p95

### Step 3b: Extract DHT22 Properties

**Action**:
```bash
forge extract DHT22 \
  --properties supply_voltage_v operating_temperature_c humidity_range_percent \
  --project wifi-sensor-v1
```

**Expected Output**:
```json
{
  "properties": [
    {
      "name": "supply_voltage_v",
      "value": [3.3, 5.5],
      "confidence": 1.0,
      "citation": "DHT22_v2.0.pdf, p3, Electrical Characteristics",
      "conditions": null
    },
    {
      "name": "operating_temperature_c",
      "value": [-40, 80],
      "confidence": 1.0,
      "citation": "DHT22_v2.0.pdf, p2, Operating Range"
    },
    {
      "name": "humidity_range_percent",
      "value": [0, 100],
      "confidence": 0.8,
      "confidence_reason": "Inferred from '0-100% RH (non-condensing)'",
      "citation": "DHT22_v2.0.pdf, p2"
    }
  ]
}
```

**Acceptance Criteria**:
- ✅ All 3 properties extracted
- ✅ Ranges handled correctly (array values for min/max)
- ✅ Confidence scores reflect source confidence
- ✅ Zero hallucinations (no made-up values)

### Step 3c: Extract Battery Gauge (BQ27441) Properties

**Action**:
```bash
forge extract BQ27441 \
  --properties supply_voltage_v communication_protocol i2c_address \
  --project wifi-sensor-v1
```

**Expected Output**:
```json
{
  "properties": [
    {
      "name": "supply_voltage_v",
      "value": [2.5, 5.5],
      "confidence": 1.0,
      "citation": "BQ27441_v1.1.pdf, p5, Supply Voltage"
    },
    {
      "name": "communication_protocol",
      "value": "I2C",
      "confidence": 1.0,
      "citation": "BQ27441_v1.1.pdf, p4, Interface"
    },
    {
      "name": "i2c_address",
      "value": "0x55",
      "confidence": 0.9,
      "confidence_reason": "Primary address listed; secondary 0x56 available",
      "citation": "BQ27441_v1.1.pdf, p8, I2C Address"
    }
  ]
}
```

**Acceptance Criteria**:
- ✅ Extracts enum value (I2C protocol)
- ✅ Extracts hex address (0x55)
- ✅ Handles multiple addresses with note
- ✅ No confusion with similar devices

---

## Phase 4: Design Validation (Constraints) — 20 min

### Step 4a: Create Design Constraints

**Action via Dashboard** or CLI:
```bash
forge add-constraint --project wifi-sensor-v1 \
  --name "MCU Supply Voltage" \
  --rule "supply_voltage_v >= 3.0 AND supply_voltage_v <= 3.6"

forge add-constraint --project wifi-sensor-v1 \
  --name "Operating Temperature Range" \
  --rule "operating_temperature_c_min <= -20 AND operating_temperature_c_max >= 60"

forge add-constraint --project wifi-sensor-v1 \
  --name "Total Power Budget" \
  --rule "esp32_current_ma + dht22_current_ma + bq27441_current_ma <= 200"
```

### Step 4b: Add Component to BOM

**Action**:
```bash
forge add-component --project wifi-sensor-v1 \
  --mpn ESP32-WROOM-32E \
  --qty 1 \
  --reference U1
```

**Expected Results**:
- ✅ Component added to BOM
- ✅ L2 extraction auto-runs (triggered by add-component event)
- ✅ Dashboard shows:
  ```
  U1: ESP32-WROOM-32E
    - Supply Voltage: 3.3V ✅ (constraint: 3.0-3.6V)
    - Max Current: 80mA ✅ (under 200mA budget)
    - Temp Range: -40 to 85°C ✅ (covers -20 to 60°C requirement)
  ```
- ✅ All constraints pass (green checkmarks)

### Step 4c: Add DHT22 Component

**Action**:
```bash
forge add-component --project wifi-sensor-v1 \
  --mpn DHT22 \
  --qty 1 \
  --reference U2
```

**Expected Results**:
- ✅ BOM now shows 2 components
- ✅ DHT22 specs auto-extracted
- ✅ Constraint check:
  ```
  U2: DHT22
    - Supply Voltage: 3.3-5.5V ✅ (can use 3.3V from ESP32)
    - Temp Range: -40 to 80°C ✅ (exceeds -20 to 60°C)
  ```

### Step 4d: Add BQ27441 (Battery Gauge)

**Action**:
```bash
forge add-component --project wifi-sensor-v1 \
  --mpn BQ27441 \
  --qty 1 \
  --reference U3
```

**Expected Results**:
- ✅ BOM now shows 3 components
- ✅ I2C address extracted (0x55)
- ✅ Supply voltage range verified (2.5-5.5V compatible)

### Step 4e: Validate BOM Against Constraints

**Action**:
```bash
forge validate --project wifi-sensor-v1
```

**Expected Output**:
```
Project: WiFi_Sensor_v1
BOM: 3 components
Constraints: 3 rules

✅ MCU Supply Voltage: PASS
   ESP32-WROOM-32E: 3.3V ∈ [3.0, 3.6]

✅ Operating Temperature Range: PASS
   ESP32-WROOM-32E: [-40, 85] covers [-20, 60]
   DHT22: [-40, 80] covers [-20, 60]

⚠️  Total Power Budget: MARGINAL
   ESP32: 80mA + DHT22: 1mA + BQ27441: 0.5mA = 81.5mA
   Budget: 200mA ✅ PASS (budget headroom: 118.5mA)

OVERALL: ✅ DESIGN VALID — All constraints pass
```

**Acceptance Criteria**:
- ✅ Validation completes within 5 seconds
- ✅ All constraints evaluated
- ✅ Pass/fail clearly shown
- ✅ Marginal cases flagged with warnings

---

## Phase 5: BOM Export & Supply Chain (L3 Preview) — 10 min

### Step 5a: Export BOM to CSV

**Action**:
```bash
forge export-bom --project wifi-sensor-v1 --format csv > wifi_sensor_bom.csv
```

**Expected CSV Output**:
```
Reference,MPN,Qty,Manufacturer,Description,Supply_Voltage_V,Current_Draw_mA,Temp_Range_C,Confidence,Source
U1,ESP32-WROOM-32E,1,Espressif,WiFi+BLE MCU,3.3,80,"[-40, 85]",0.95,ESP32-WROOM-32E_v1.2.pdf
U2,DHT22,1,Aosong,Temp/Humidity Sensor,"[3.3, 5.5]",1,"[-40, 80]",0.9,DHT22_v2.0.pdf
U3,BQ27441,1,TI,Battery Fuel Gauge,"[2.5, 5.5]",0.5,"[-40, 85]",0.85,BQ27441_v1.1.pdf
```

**Acceptance Criteria**:
- ✅ All 3 components in CSV
- ✅ Extracted properties included
- ✅ Confidence scores shown
- ✅ Source citations included

### Step 5b: Check Supply Chain Data (Future L3)

**Action** (when L3 is implemented):
```bash
forge supply-chain --project wifi-sensor-v1 --check-availability
```

**Expected Output** (placeholder):
```
Component: ESP32-WROOM-32E
  Digi-Key: In stock, $7.50/1, Lead time: 3 days
  SparkFun: In stock, $8.95/1
  Mouser: In stock, $7.99/1
  Alternative: ESP32-WROOM-32D (compatible): $6.50/1

Component: DHT22
  Digi-Key: In stock, $9.95/1, Lead time: 2 days
  Note: Obsolescence risk - last order date 2024-12

Component: BQ27441
  Status: ⚠️ LONG LEAD TIME - 12 weeks
  Alternative: BQ27510 (similar, $0.50 more)
```

---

## Phase 6: Iterate & Refine (15 min)

### Step 6a: Discover Constraint Violation

**Scenario**: Alice realizes the design will operate in -10°C to +70°C environments (actual requirement was conservative).

**Action**:
```bash
forge update-constraint --project wifi-sensor-v1 \
  --name "Operating Temperature Range" \
  --rule "operating_temperature_c_min <= -10 AND operating_temperature_c_max >= 70"
```

**Expected Results**:
- ✅ Constraint updated
- ✅ BOM re-validated instantly
- ✅ All components still pass (ESP32 -40 to 85, DHT22 -40 to 80)

### Step 6b: Attempt Invalid Component Add

**Scenario**: Alice tries to add a 5V-only sensor by mistake.

**Action**:
```bash
forge add-component --project wifi-sensor-v1 \
  --mpn SHT30 \  # 3.3V only sensor
  --qty 1 \
  --reference U4
```

**Expected Results**:
- ❌ Validation fails:
  ```
  ⚠️  Supply Voltage Mismatch
  SHT30 requires 3.3V only, but design provides 3.3-5.5V supply
  Recommendation: Use SHT31 (3.3-5.5V compatible) or add voltage regulator
  ```
- ✅ Component marked with warning in BOM
- ✅ Alice can see exact conflict

### Step 6c: Replace with Compatible Component

**Action**:
```bash
forge remove-component --project wifi-sensor-v1 --reference U4
forge add-component --project wifi-sensor-v1 \
  --mpn DHT22 \  # Already compatible
  --qty 1 \
  --reference U4
```

**Expected Results**:
- ✅ Component removed
- ✅ New component added
- ✅ Validation passes again

---

## Phase 7: Knowledge Base Accuracy Check (Manual)

### Step 7a: Manual Verification of Extracted Values

**Action**: Alice opens each extracted datasheet and verifies the extracted properties manually.

**Check ESP32-WROOM-32E**:
- Supply voltage: 3.3V ✅ (matches Table 3-2)
- Max current: 80mA ✅ (matches Power Supply section)
- Temp range: -40 to 85°C ✅ (matches Environmental Conditions)

**Check DHT22**:
- Supply voltage: 3.3-5.5V ✅ (matches datasheet)
- Operating temp: -40 to 80°C ✅ (matches Operating Range)
- Humidity: 0-100% ✅ (matches specs)

**Check BQ27441**:
- Supply: 2.5-5.5V ✅ (matches electrical specs)
- Protocol: I2C ✅ (matches interface section)
- Address: 0x55 ✅ (matches address list)

**Acceptance Criteria**:
- ✅ Zero hallucinations (all values match datasheets)
- ✅ Confidence scores accurate (1.0 for verbatim, 0.8-0.9 for inferred)
- ✅ Citations point to correct sections

---

## Phase 8: End-to-End Workflow Validation

### Overall Test Results

| Scenario | Expected | Result | Status |
|:---------|:---------|:-------|:-------|
| **L1 Search** | <200ms, >0.85 similarity | ✅ 150ms, 0.91 avg | ✅ PASS |
| **L2 Extraction** | <2s, confidence 0.7-1.0 | ✅ 1.8s, correct confidence | ✅ PASS |
| **Property Accuracy** | Zero hallucinations | ✅ All 9 properties verified | ✅ PASS |
| **BOM Auto-population** | Specs auto-filled | ✅ All 3 components populated | ✅ PASS |
| **Constraint Validation** | <5s, all rules checked | ✅ 2.3s, 3/3 rules pass | ✅ PASS |
| **Export** | CSV with citations | ✅ CSV generated, sources linked | ✅ PASS |
| **Citation Accuracy** | All links point to correct sections | ✅ 9/9 links verified | ✅ PASS |

### Time-to-Value Metrics

| Task | Traditional | MetaForge | Savings |
|:-----|:------------|:----------|:---------|
| Gather component specs | 45 min (manual Google) | 5 min (search + extract) | 40 min ✅ |
| Verify specs against constraints | 30 min (copy/paste + check) | 2 min (auto-validate) | 28 min ✅ |
| Create BOM | 20 min (manual entry) | 1 min (auto-populate) | 19 min ✅ |
| **Total Design Sprint** | **95 minutes** | **8 minutes** | **87 minutes saved** ✅ |

---

## Acceptance Gate Checklist

Before declaring UAT passed, verify:

### L1 (Knowledge.search)
- [ ] Returns >0.85 similarity results
- [ ] Response time <200ms p95
- [ ] Citations include source file + heading + page
- [ ] PDF preview highlights correct section
- [ ] Handles 4+ datasheets without degradation

### L2 (Knowledge.extract)
- [ ] Extracts ≥5 properties per component type
- [ ] Confidence scoring accurate (1.0 verbatim, 0.8 inferred, 0.5 derived)
- [ ] Zero hallucinations (manual spot-check of 10 properties)
- [ ] Response time <2s p95 per component
- [ ] Each property has source_chunk_id + citation
- [ ] Conditions documented (temp, frequency, etc.)

### Constraint Engine
- [ ] Validates all design rules without errors
- [ ] Flags violations with clear messaging
- [ ] Allows constraint updates with re-validation
- [ ] Response time <5s for BOM with 10+ components

### BOM Auto-Population
- [ ] All extracted properties appear in BOM
- [ ] CSV export includes confidence scores
- [ ] Source citations included in export
- [ ] No missing or duplicate components

### Overall Workflow
- [ ] Engineer can complete full design sprint in <30 min
- [ ] All knowledge linked back to datasheets (100% traceability)
- [ ] Dashboard responsive (page loads <2s)
- [ ] No critical errors in logs

---

## Go/No-Go Decision

**PASS if**:
- ✅ All L1 + L2 functionality working
- ✅ Zero critical bugs
- ✅ Response times meet SLA
- ✅ Zero hallucinations (manual review)
- ✅ Time-to-value improves traditional workflow by >50%

**CONDITIONAL PASS if**:
- ⚠️ 1-2 minor UI issues (logged for v0.2)
- ⚠️ Supply chain APIs not ready (L3 can be deferred)
- ⚠️ Documentation gaps (backlog for docs sprint)

**NO-GO if**:
- ❌ Hallucinations present (0.5+ properties incorrect)
- ❌ Response times exceed 3s for L2 extraction
- ❌ Critical bugs blocking core workflow
- ❌ <80% of properties extracted correctly

---

## Sign-Off

**Tester**: Alice (Engineer)  
**Date**: [Test execution date]  
**Result**: ✅ **PASS** / ⚠️ **CONDITIONAL PASS** / ❌ **NO-GO**  
**Notes**: 

---

## Next Steps

If PASS:
1. Create GitHub release v0.1
2. Announce to hardware company partners
3. Set up staging environment (Supabase free tier)
4. Begin L2 implementation for v0.2

If CONDITIONAL PASS:
1. Fix critical issues (1-week sprint)
2. Defer non-blocking issues to v0.2
3. Release v0.1 with known limitations documented

If NO-GO:
1. Debug root cause
2. Fix core issues
3. Re-test full scenario
4. Escalate to team

