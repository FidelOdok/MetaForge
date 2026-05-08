"""Generate `tests/uat/scenarios/tier1/datasheets-real.md` from the
ground-truth files under `tests/fixtures/datasheets/`. Run this any
time `<mpn>.gt.yaml` is added or edited so the scenario file stays in
sync.

The output file follows the parsing contract in
`.claude/agents/uat-validator.agent.md` lines 67–110 (every
`## Scenario:` block has Validates / Tier / Given / When / Then).
"""

from __future__ import annotations

import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "datasheets"
SCENARIO_PATH = REPO_ROOT / "tests" / "uat" / "scenarios" / "tier1" / "datasheets-real.md"

ORDER = [
    "rp2040",
    "bme280",
    "tps62840",
    "stm32h723vgt6",
    "esp32-wroom-32",
    "nrf52840",
    "lm2596",
    "mcp2515",
]

PREAMBLE = """# Tier-1 — real-datasheet retrieval QA (KB-DS)

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
"""

ACCEPTANCE = """
---

## Acceptance

- All 80 scenarios run in a single `/uat-cycle12 --tier 1 --only "KB-DS-"` invocation.
- Baseline target on first run: ≥ 53 / 80 PASS. Failures are diagnostic
  signal, not test-harness regressions — capture the top-3 chunk
  contents in the report so the failure mode (retrieval ranking,
  extraction quality, chunk boundary) is immediately diagnosable.
- Verdict roll-up updates `docs/uat/kb-test-plan.md` §11.
"""

SCENARIO_TEMPLATE = """
---

## Scenario: {qid} — {mpn} {cat}
Validates: MET-346, MET-293, MET-335
Tier: 1

### Given
- Fixture `tests/fixtures/datasheets/{mpn_lower}.txt` is present and its
  sha256 matches the pin for `{mpn}` in
  `tests/fixtures/datasheets/manifest.yaml`.
- Source path for ingest: `datasheet://{mpn_lower}`.
- Expected citation section (soft assertion): `{section}`.

### When
1. Read the fixture file off disk and call `mcp__metaforge__knowledge_ingest` with:
   - `content`: the fixture file contents
   - `source_path`: `datasheet://{mpn_lower}`
   - `knowledge_type`: `component`
   - `metadata`: `{{ "vendor": "{vendor}", "mpn": "{mpn}" }}`
2. Call `mcp__metaforge__knowledge_search` with:
   - `query`: `{question}`
   - `top_k`: `3`
   - `knowledge_type`: `component`

### Then
- Step 2 returns ≥ 1 hit.
- Top-1 hit's `source_path == "datasheet://{mpn_lower}"`.
- Top-1 hit's `content` contains the literal substring `"{expect}"`.
- Top-1 hit's `metadata.mpn == "{mpn}"`.
- Top-1 hit's `heading` is non-empty (heading-aware chunking honoured).
"""


def main() -> None:
    parts: list[str] = [PREAMBLE]
    total = 0
    for mpn_lower in ORDER:
        gt_path = FIXTURE_ROOT / f"{mpn_lower}.gt.yaml"
        gt = yaml.safe_load(gt_path.read_text(encoding="utf-8"))
        mpn = gt["mpn"]
        vendor = gt["vendor"]
        family = gt["family"]
        n = len(gt["queries"])
        total += n
        parts.append(
            f"\n## {mpn} — {vendor} ({family})\n\nFixture: "
            f"`tests/fixtures/datasheets/{mpn_lower}.txt`. {n} queries.\n"
        )
        for q in gt["queries"]:
            parts.append(
                SCENARIO_TEMPLATE.format(
                    qid=q["id"],
                    mpn=mpn,
                    mpn_lower=mpn_lower,
                    vendor=vendor,
                    cat=q["category"],
                    section=q.get("expected_section", ""),
                    question=q["question"],
                    expect=q["expected_substring"],
                )
            )
    parts.append(ACCEPTANCE)
    SCENARIO_PATH.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {SCENARIO_PATH.relative_to(REPO_ROOT)} — {total} scenarios")


if __name__ == "__main__":
    main()
