---
layout: default
title: UAT Quick Reference — Testing Checklist
description: One-page checklist for UAT testing (print-friendly)
nav_order: 41
---

# UAT Quick Reference Checklist
{: .no_toc }

Print this page and use as a checklist during UAT testing.

---

## Test Setup (10 min)

```bash
# Terminal 1: Start dashboard
cd MetaForge
docker compose up -d postgres neo4j gateway dashboard

# Terminal 2: Create test project
forge new-project WiFi_Sensor_v1 "Battery-powered WiFi sensor"

# Terminal 3: Ingest datasheets
forge ingest tests/fixtures/datasheets/ESP32-WROOM-32E_v1.2.pdf --project wifi-sensor-v1
forge ingest tests/fixtures/datasheets/DHT22_v2.0.pdf --project wifi-sensor-v1
forge ingest tests/fixtures/datasheets/BQ27441_v1.1.pdf --project wifi-sensor-v1
forge ingest tests/fixtures/datasheets/AMS1117_v3.0.pdf --project wifi-sensor-v1

# Verify
forge sources list --project wifi-sensor-v1
```

**Setup Status**:
- [ ] Docker services running
- [ ] Project created
- [ ] 4 datasheets ingested
- [ ] Dashboard accessible at `http://localhost:5173`

---

## Phase 1: L1 Search (25 min)

### Search 1: ESP32 Voltage & Current
```bash
Query: "ESP32 operating voltage maximum current draw specifications"
```

**Expected Results**:
- [ ] Returns ≥2 results with similarity >0.85
- [ ] Top result: ESP32 electrical specifications
- [ ] Includes: supply voltage (3.3V), max current (80mA)
- [ ] Response time: <200ms
- [ ] Citation shows: file + heading + page number

**Manual Verification**:
- [ ] Click PDF preview, verify chunk location
- [ ] Open full datasheet, confirm values match

---

### Search 2: DHT22 Specifications
```bash
Query: "DHT22 temperature humidity sensor operating range voltage"
```

**Expected Results**:
- [ ] Similarity >0.85
- [ ] Includes: voltage (3.3-5.5V), temp range (-40 to 80°C), humidity (0-100%)
- [ ] Response time: <200ms

---

### Search 3: Power Management
```bash
Query: "BQ27441 fuel gauge I2C battery voltage monitoring"
```

**Expected Results**:
- [ ] Finds BQ27441 datasheet
- [ ] Includes: supply voltage, I2C protocol, typical power consumption
- [ ] Response time: <200ms

**L1 Summary**:
- [ ] All 3 searches return relevant results
- [ ] No false positives or off-topic results
- [ ] All similarities >0.85
- [ ] All response times <200ms

---

## Phase 2: L2 Extraction (45 min)

### Extract 1: ESP32-WROOM-32E
```bash
forge extract ESP32-WROOM-32E \
  --properties supply_voltage_v max_current_ma operating_temperature_range_c \
  --project wifi-sensor-v1 \
  --confidence-threshold 0.7
```

**Check Output**:
- [ ] Extracts 3 properties
- [ ] Confidence scores: 1.0 (voltage), 0.9 (current), 1.0 (temp)
- [ ] Each has source_chunk_id + citation
- [ ] Conditions documented (e.g., "temp=25C")
- [ ] Response time: <2s
- [ ] No hallucinations (compare to datasheet manually)

**Manually Verify Values**:
- [ ] supply_voltage_v = 3.3V ✅ (matches Table 3-2)
- [ ] max_current_ma = 80mA ✅ (matches Power Supply section)
- [ ] operating_temperature_range_c = [-40, 85] ✅ (matches datasheet)

---

### Extract 2: DHT22
```bash
forge extract DHT22 \
  --properties supply_voltage_v operating_temperature_c humidity_range_percent \
  --project wifi-sensor-v1
```

**Check Output**:
- [ ] Extracts 3 properties with correct values
- [ ] Voltage as range: [3.3, 5.5]
- [ ] Temperature as range: [-40, 80]
- [ ] Humidity: [0, 100]
- [ ] Confidence: 0.8-1.0 (ranges slightly inferred = 0.8 acceptable)
- [ ] Zero hallucinations (manual spot-check)

---

### Extract 3: BQ27441
```bash
forge extract BQ27441 \
  --properties supply_voltage_v communication_protocol i2c_address \
  --project wifi-sensor-v1
```

**Check Output**:
- [ ] supply_voltage_v: [2.5, 5.5] ✅
- [ ] communication_protocol: "I2C" ✅
- [ ] i2c_address: "0x55" ✅
- [ ] Confidence: 0.9-1.0
- [ ] Citation points to correct sections

**L2 Summary**:
- [ ] All 9 properties extracted (3 components × 3 properties)
- [ ] Confidence scores accurate (1.0 = verbatim, 0.8-0.9 = inferred)
- [ ] Zero hallucinations (compare all 9 to datasheets)
- [ ] All response times <2s
- [ ] All citations traceable to source chunks

---

## Phase 3: BOM Auto-Population (20 min)

### Add Components
```bash
# Add ESP32
forge add-component --project wifi-sensor-v1 --mpn ESP32-WROOM-32E --qty 1 --ref U1

# Add DHT22
forge add-component --project wifi-sensor-v1 --mpn DHT22 --qty 1 --ref U2

# Add BQ27441
forge add-component --project wifi-sensor-v1 --mpn BQ27441 --qty 1 --ref U3
```

**Check BOM After Each Add**:
- [ ] Component appears in BOM
- [ ] Extracted properties auto-populated
- [ ] Confidence scores shown
- [ ] Source citations included
- [ ] Dashboard updates in real-time

---

### Add Constraints
```bash
forge add-constraint --project wifi-sensor-v1 \
  --name "MCU Supply Voltage" \
  --rule "supply_voltage_v >= 3.0 AND supply_voltage_v <= 3.6"

forge add-constraint --project wifi-sensor-v1 \
  --name "Operating Temperature" \
  --rule "operating_temperature_c_min <= -20 AND operating_temperature_c_max >= 60"

forge add-constraint --project wifi-sensor-v1 \
  --name "Total Power" \
  --rule "esp32_current_ma + dht22_current_ma + bq27441_current_ma <= 200"
```

**Check Validation**:
- [ ] MCU constraint: PASS (3.3V ∈ [3.0, 3.6])
- [ ] Temp constraint: PASS (ESP32 covers [-40, 85], DHT22 covers [-40, 80])
- [ ] Power constraint: PASS (80 + 1 + 0.5 = 81.5mA < 200mA)
- [ ] Validation time: <5s

**Dashboard Check**:
- [ ] BOM shows 3 components with ✅ icons
- [ ] Constraint panel shows 3 rules, all PASS
- [ ] No errors or warnings (except marginal cases noted)

---

## Phase 4: BOM Export (10 min)

```bash
forge export-bom --project wifi-sensor-v1 --format csv > wifi_sensor_bom.csv
cat wifi_sensor_bom.csv
```

**Check CSV Output**:
- [ ] 3 components listed
- [ ] All columns present: Reference, MPN, Qty, Voltage, Current, Temp, Confidence, Source
- [ ] Values match extracted properties
- [ ] Confidence scores included
- [ ] Source citations included (e.g., "ESP32-WROOM-32E_v1.2.pdf")

---

## Phase 5: Accuracy Verification (Manual)

**Compare extracted values to datasheets**:

### ESP32-WROOM-32E
| Property | Extracted | Datasheet | Match? |
|:---------|:----------|:----------|:-------|
| Supply voltage | 3.3V | Table 3-2, p14 | [ ] ✅ |
| Max current | 80mA | Power Supply, p15 | [ ] ✅ |
| Temp range | -40 to 85°C | Environmental, p16 | [ ] ✅ |

### DHT22
| Property | Extracted | Datasheet | Match? |
|:---------|:----------|:----------|:-------|
| Supply voltage | 3.3-5.5V | Electrical, p3 | [ ] ✅ |
| Temp range | -40 to 80°C | Operating Range, p2 | [ ] ✅ |
| Humidity | 0-100% RH | Specifications, p2 | [ ] ✅ |

### BQ27441
| Property | Extracted | Datasheet | Match? |
|:---------|:----------|:----------|:-------|
| Supply voltage | 2.5-5.5V | Supply Voltage, p5 | [ ] ✅ |
| Protocol | I2C | Interface, p4 | [ ] ✅ |
| I2C address | 0x55 | Address List, p8 | [ ] ✅ |

**Accuracy Check**: 
- [ ] All 9 properties verified against datasheets
- [ ] Zero hallucinations
- [ ] Confidence scores reflect source accuracy

---

## Phase 6: Performance Metrics

Time the following (use stopwatch):

| Operation | Expected | Actual | Pass? |
|:----------|:---------|:-------|:------|
| **L1 Search** | <200ms | ___ ms | [ ] ✅ |
| **L2 Extract** (1 component) | <2s | ___ s | [ ] ✅ |
| **Add Component** | <2s | ___ s | [ ] ✅ |
| **Validate BOM** | <5s | ___ s | [ ] ✅ |
| **Export BOM** | <1s | ___ s | [ ] ✅ |

---

## Final Checklist: Go/No-Go

### L1 Functionality
- [ ] Search returns >0.85 similarity
- [ ] Response time <200ms
- [ ] Citations accurate
- [ ] No false positives

### L2 Functionality
- [ ] Extracts ≥5 properties per component
- [ ] Confidence scoring accurate (1.0/0.8/0.5)
- [ ] Zero hallucinations
- [ ] Response time <2s per component
- [ ] Source chunks linked

### BOM & Constraints
- [ ] Properties auto-populated on add
- [ ] Constraints validate without error
- [ ] Validation time <5s
- [ ] CSV export includes all metadata

### Overall Quality
- [ ] All values match datasheets (manual spot-check)
- [ ] Dashboard responsive
- [ ] No critical errors in logs
- [ ] Time-to-design improved (compare to manual process)

---

## Overall Result

**Date**: ____________  
**Tester**: ____________  
**Test Environment**: Local dev server  
**Duration**: ___ minutes  

### Result:
- [ ] ✅ **PASS** — All criteria met, ready for v0.1 release
- [ ] ⚠️ **CONDITIONAL PASS** — Minor issues logged, v0.1 acceptable
- [ ] ❌ **NO-GO** — Critical issues block release

### Issues Found:
```
1. ______________________________________
2. ______________________________________
3. ______________________________________
```

### Sign-Off:
**Tester Signature**: ________________________  
**Date**: ________________________  
**Recommendation**: _________________________

---

## Next Steps If PASS:
- [ ] Tag v0.1 release
- [ ] Announce to partners
- [ ] Set up staging environment
- [ ] Plan L2 extension testing

## Next Steps If NO-GO:
- [ ] Debug root cause
- [ ] File GitHub issue with reproduction steps
- [ ] Re-test full scenario after fix
- [ ] Escalate if blocking

