# Knowledge Base Maturity Roadmap (L1-L4)

**Alignment with Implementation Progress** (Updated May 2026)

---

## Executive Summary

| Level | Target | Status | Implementation |
|:------|:--------|:-------|:----------------|
| **L1** | May 2026 | ✅ SHIPPED | LightRAG search, pgvector embeddings, knowledge graph |
| **L2** | June 2026 | 🔄 IN PROGRESS | MET-445 (table extraction), MET-444 (PDF tables), LLM tiers pending |
| **L3** | July 2026 | ⏳ NEXT | Twin schema, supply chain APIs, auto-populate |
| **L4** | August 2026 | ✅ FOUNDATION | MET-446 (staleness detection) foundations shipped |

---

## L1: Passive RAG (✅ SHIPPED)

**Status**: Production ready. All components operational.

### Delivered Components

| Feature | Implementation | Issue | Status |
|:--------|:---------------|:------|:-------|
| **Semantic search** | LightRAG hybrid (semantic + BM25) | MET-433 | ✅ Live |
| **Datasheet ingestion** | PDF parser with table extraction | MET-444 | ✅ Live |
| **Embeddings** | pgvector with 3 models (local, OpenAI, Anthropic) | MET-335 | ✅ Live |
| **Version tracking** | Supersedes chain for datasheet revisions | MET-447 | ✅ Live |
| **Knowledge graph** | Neo4j DESCRIBES edges | MET-312 | ✅ Live |
| **MCP tools** | `knowledge.search`, `knowledge.list_sources`, `knowledge.delete_by_source` | MET-313 | ✅ Live |
| **CLI commands** | `forge knowledge ingest-datasheet`, `forge sources list/show/delete` | MET-443 | ✅ Live |

### Performance Metrics
- **Search latency**: <200ms p95 ✅
- **Ingestion**: 100+ datasheets tested ✅
- **Citation accuracy**: 100% traceable to source chunks ✅

### UAT Ready
- See: [`docs/testing/uat-iot-sensor-board.md`](../testing/uat-iot-sensor-board.md) Phase 1-2 (L1 Search)
- See: [`docs/testing/uat-quick-reference.md`](../testing/uat-quick-reference.md) (checklist)

---

## L2: Structured Extraction (🔄 IN PROGRESS)

**Status**: Tier 1 (table-based) shipped; Tier 2-3 (LLM-based) in progress.

### Current Implementation (Tier 1)

| Feature | Implementation | Issue | Status |
|:--------|:---------------|:------|:-------|
| **Table extraction** | Structured rows from PDFs | MET-444 | ✅ Shipped |
| **Property schema** | `ExtractedProperty` dataclass | MET-445 | ✅ Shipped |
| **Verbatim confidence** | Literal table-cell matches (1.0) | MET-445 | ✅ Shipped |
| **Citation tracking** | source_chunk_id → datasheet chunk | MET-445 | ✅ Shipped |
| **Conditions field** | Store conditions (temperature, frequency) | MET-445 | ✅ Designed |

### Module Location
```
digital_twin/knowledge/property_extractor.py
├─ ExtractionMethod: VERBATIM, LLM_INFERRED, DERIVED
├─ ExtractedProperty: value, unit, confidence, citation, conditions
└─ extract_property_from_tables(): Pure function, testable
```

### Pending: Tiers 2-3 (LLM-based)

| Tier | Source | Confidence | Status |
|:-----|:-------|:-----------|:-------|
| **Tier 1** | Literal table cells | 1.0 | ✅ Shipped |
| **Tier 2** | LLM-inferred from chunks | 0.6–0.8 | ⏳ Waiting for LLM wiring (MET-422 extended) |
| **Tier 3** | Derived from related fields | 0.4–0.6 | ⏳ Waiting for constraint engine feedback |

### Gap to Close Before L3 Can Ship

**What's needed**:
1. Wire Claude API into `property_extractor.py` (add `llm_inferred` method)
2. Implement confidence scoring rules (verbatim=1.0, inferred=0.8, derived=0.5)
3. Add hallucination detection (sanity-check values against ranges)
4. Expose via MCP tool: `knowledge.extract(component, properties_list)`
5. Expose via CLI: `forge extract ESP32-WROOM-32E --properties voltage current temp`

**Effort**: ~20 hours (wire LLM, tests, MCP integration)

### UAT Ready
- See: [`docs/testing/uat-iot-sensor-board.md`](../testing/uat-iot-sensor-board.md) Phase 3 (L2 Extraction)
- Assumes Tier 2 LLM integration is complete

---

## L3: Auto-populate BOM (⏳ NEXT)

**Status**: Backlog. Ready to start once L2 ships.

### Planned Components

| Feature | Implementation | Dependency | Status |
|:--------|:---------------|:-----------|:-------|
| **Twin schema** | Component, Property, Lifecycle, Distributor nodes | L2 + Neo4j | ⏳ Planned |
| **Auto-extract on add** | Event: add-component → extract specs → Twin | L2 + Kafka | ⏳ Planned |
| **Constraint validation** | Re-check design rules on BOM change | Constraint engine | ⏳ Planned |
| **Supply chain APIs** | Digi-Key, Nexar integration | External APIs | ⏳ Planned (MET-424) |
| **Cost breakdown** | Unit cost, BOM total, volume discounts | Supply chain | ⏳ Planned |
| **Risk flags** | EOL, NRND, low-confidence specs | Supply chain + L2 | ⏳ Planned |
| **Alternative parts** | Suggest substitutes by price/availability | Supply chain | ⏳ Planned |

### Architecture

```
Engineer adds component
  ↓
Event: add_component(mpn=..., qty=..., project_id=...)
  ↓
L2 Extraction: knowledge.extract(mpn, ["supply_voltage", "current", ...])
  ↓
Twin Schema: Component -[:HAS_PROPERTY]-> Property (with confidence)
  ↓
Constraint Engine: Validate all rules
  ↓
Supply Chain API: Query pricing, stock, alternates
  ↓
Dashboard: BOM updated with risk flags + alternatives
```

### Gap Analysis
- **Event flow not wired** (need Kafka topic + listener)
- **Twin schema incomplete** (need Property edge definitions)
- **Supply chain APIs not integrated** (Digi-Key auth, rate limiting)
- **Dashboard BOM view outdated** (doesn't show confidence scores)

**Effort**: ~60 hours (2 engineers, 2 sprints)

### Unblocks
- ✅ Component reuse (Twin stores usage history)
- ✅ Compliance checking (can query "all capacitors > 100µF")
- ✅ Supply chain risk (know when to substitute)
- ✅ Cost optimization (pick alternatives by price)

---

## L4: Staleness Detection (✅ FOUNDATION)

**Status**: Foundations shipped (MET-446). Workflow integration pending.

### Delivered

| Feature | Implementation | Issue | Status |
|:--------|:---------------|:------|:-------|
| **Version detection** | Hash-based revision comparison | MET-446 | ✅ Shipped |
| **Staleness tracker** | `SourceFreshness` schema | MET-446 | ✅ Shipped |
| **Monitoring loop** | Weekly checks (Temporal job ready) | MET-446 | ✅ Shipped |

### Pending

| Feature | Purpose | Status |
|:--------|:---------|:--------|
| **Alert UI** | "Datasheet updated — Re-ingest? [1 click]" button | ⏳ Dashboard integration |
| **Re-extract pipeline** | Trigger L2 on new datasheet version | ⏳ Waiting for L2 |
| **Property diffing** | Show "spec changed: 3.0V → 3.3V" | ⏳ Planned |
| **Design revalidation** | Auto re-check constraints after update | ⏳ Planned |
| **Audit trail** | Store historical versions + approvals | ⏳ Planned |

### Gap Analysis
- **No scheduler configured** (Temporal jobs ready but not hooked up)
- **No dashboard integration** (UI for re-ingest button)
- **No design revalidation** (constraint engine doesn't re-run)

**Effort**: ~30 hours (integrate with L2 + dashboard + scheduler)

### Unblocks
- ✅ Compliance (prove "we used v1.2 on 2026-06-15")
- ✅ Supply chain resilience (catch EOL notices before production)
- ✅ Design quality (always using current specs)

---

## Implementation Timeline

### v0.1 (June 2026) — L1 + Tier 1 L2
```
✅ L1 Search (shipped)
✅ Datasheet ingest (shipped)
✅ Table extraction (shipped)
✅ Tier 1 property extraction (verbatim from tables)
❌ Tier 2-3 (LLM-based) — requires Claude integration
❌ BOM auto-populate — requires L2 complete
❌ Supply chain — not included
```

### v0.2 (July 2026) — L2 Complete
```
✅ L1 + L2 complete (Tier 1 + 2 + 3)
✅ Confidence scoring (1.0/0.8/0.5)
✅ MCP tool + CLI for extraction
✅ Hallucination detection
❌ BOM auto-populate — v0.3
❌ Supply chain — v0.3
```

### v0.3 (August 2026) — L3 Complete
```
✅ L1-L3 complete
✅ Auto-populate BOM (extraction → Twin → validate)
✅ Supply chain APIs (pricing, stock, alternatives)
✅ Multi-user collaboration
❌ Staleness detection — v0.4
```

### v0.4 (September 2026) — L4 Complete
```
✅ L1-L4 complete
✅ Staleness detection + re-ingest
✅ Design revalidation on updates
✅ Audit trail (compliance-ready)
✅ Full hardware product workflow
```

---

## Critical Path for v0.1 Ship

**Blocker**: L2 Tier 2-3 (LLM integration)

**To unblock**:
1. Wire Claude API into `property_extractor.py`
2. Test on 10+ component types (manual verification)
3. Deploy to dev server

**Once unblocked**:
- BOM auto-populate becomes possible (L3)
- Component search becomes powerful (with extracted specs)
- Constraint engine can trust component properties

---

## Testing & Validation

### UAT Scenarios
- **Phase 1-2**: L1 Search testing → See [`uat-iot-sensor-board.md`](../testing/uat-iot-sensor-board.md)
- **Phase 3**: L2 Extraction → 9 properties extracted, confidence verified
- **Phase 4**: Constraint validation → All rules pass
- **Phase 5**: BOM export → CSV with citations

### Go/No-Go Criteria for L2
- ✅ Extracts ≥5 properties per component type
- ✅ Confidence scoring accurate (1.0 verbatim, 0.8 inferred, 0.5 derived)
- ✅ Zero hallucinations (manual review of 50 properties)
- ✅ <2s latency per component
- ✅ Source chunks fully traceable

---

## See Also

- **[Knowledge Maturity Ladder](../architecture/knowledge-maturity-ladder.md)** (MetaForge-Planner)
- **[UAT IoT Sensor Board](../testing/uat-iot-sensor-board.md)** (detailed test scenario)
- **[UAT Quick Reference](../testing/uat-quick-reference.md)** (print-friendly checklist)
- **[property_extractor.py](../../digital_twin/knowledge/property_extractor.py)** (Tier 1 implementation)
- **[LightRAG Service](../../digital_twin/knowledge/lightrag_service.py)** (L1 implementation)

