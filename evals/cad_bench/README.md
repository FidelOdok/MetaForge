# External-benchmark CAD evals (MET-704+)

A third eval suite, alongside the design-flow (`evals/scenarios/`) and
chat context-engineering (`evals/chat_scenarios/`) suites: scores MetaForge's
CadQuery support against **real, externally-authored** text-to-CAD data
rather than hand-written scenarios.

See `.artifacts/` (this session) for the research behind which dataset —
short version: the text-to-CAD dataset landscape is a DeepCAD-derived
family with machine-generated captions; **CAD-Coder** (NeurIPS 2025,
Apache-2.0) is the most directly usable one for MetaForge specifically,
since it pairs natural-language prompts with reference **CadQuery Python
scripts** — the exact format `lower_design_ir_cadquery()` produces —
rather than an intermediate command-sequence representation.

## Phase 1 (done): sandbox-compatibility (`run_cad_bench.py`)

**What it measures**: does MetaForge's `cadquery.execute_script` sandbox
correctly execute real-world CadQuery code? This is deliberately narrower
than a generation-quality eval — it executes each sample's **reference**
script (not anything MetaForge generated) and records executability +
geometry.

This is exactly how MET-704 was found: 2 of the first 25 sampled scripts
failed with `NameError: name 'Workplane' is not defined` before that fix
landed (a `from cadquery import Workplane, Vector, ...` bare-name gap, the
same bug class as MET-645/649/688).

```bash
# Inside the gateway container (needs tool_registry/skill_registry on the
# path and a real cadquery-adapter reachable):
docker exec metaforge-gateway-1 python evals/cad_bench/run_cad_bench.py
```

Writes `evals/reports/cad_bench_latest.json` (gitignored, same convention
as the other two suites): per-sample executability + volume/bounding-box,
plus a summary `executable_rate`.

### Data

`samples/cad_coder_validation_sample.json` — 25 examples, deterministically
sampled (seed 42) from CAD-Coder's **validation** split (not training data,
so a future generation-quality eval isn't scoring against examples the
underlying Text2CAD/DeepCAD lineage may have trained on). See
`manifest.yaml` for exact provenance (source URL, sha256, license).

`fetch_dataset.py` re-derives this sample from scratch (fetches the full
8817-example validation file into a gitignored `.cache/cad_bench/`,
re-samples with the same seed) — provable reproducible, not hand-curated:

```bash
python evals/cad_bench/fetch_dataset.py             # fetch + verify sha256
python evals/cad_bench/fetch_dataset.py --resample  # + regenerate the sample
```

## Phase 2+ (planned, not yet built)

See the MetaForge-Planner-style plan in this session's own summary for the
full breakdown. In short:

1. **Generation-quality eval** — feed each sample's **prompt** (not its
   reference script) through MetaForge's actual text-to-CAD path, execute
   the result, and compare against the reference's geometry
   (volume/bounding-box delta) rather than just checking the reference
   alone executes. This is the eval that actually scores MetaForge's own
   output, not just sandbox compatibility.
2. **Lowering-pass-only vs. full-pipeline** — phase 2 can run at two
   levels: hand-author a Design IR from the prompt (isolates
   `lower_design_ir_cadquery()` specifically, the thing MET-689→700 built)
   vs. route through the real chat harness so an LLM authors the IR itself
   via tool calls (tests the whole pipeline, including IR-authoring
   quality).
3. **Script-quality judging** — reuse `evals/judge.py`'s existing
   structured-verdict pattern to score generated CadQuery for
   idiomaticity/correctness beyond raw executability, once phase 2 exists
   to judge.
4. **CAD-Coder train_high / Text-to-CadQuery dataset** — larger/alternate
   samples once the phase-1/2 pipeline is proven on this first slice.
5. **Wire into `trend.py`/`nightly.sh`** — this suite's report already
   follows the same JSON shape (`suite`, `sample_count`, a rate/summary
   field, per-item `results`) the other two suites use, so regression
   tracking should need zero changes to `trend.py` once there's more than
   one report to diff.
