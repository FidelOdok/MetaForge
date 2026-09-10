"""Sandbox-compatibility eval against the CAD-Coder dataset (MET-704, phase 1).

Executes each sample's REFERENCE CadQuery script (an externally-authored,
real-world script -- not anything MetaForge generated) through the real
cadquery-adapter and records whether it executes cleanly plus its geometry.

**What this measures**: only whether MetaForge's `cadquery.execute_script`
sandbox can run real-world CadQuery code. It does NOT test MetaForge's own
text-to-CAD generation quality -- that's phase 2+ (see README.md).

This is exactly how the MET-704 sandbox gap (`from cadquery import
Workplane, ...` not pre-bound) was found: 2/25 of this sample's scripts
failed before that fix.

Run inside the gateway container (needs `tool_registry`/`skill_registry` on
the path and a real cadquery-adapter reachable):

    docker exec metaforge-gateway-1 python evals/cad_bench/run_cad_bench.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLE = REPO_ROOT / "evals" / "cad_bench" / "samples" / "cad_coder_validation_sample.json"
DEFAULT_OUT = REPO_ROOT / "evals" / "reports" / "cad_bench_latest.json"

_ASSIGN_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=", re.MULTILINE)


def bridge_to_result(script: str) -> str:
    """Append ``result = <last-assigned name>``.

    CAD-Coder's reference scripts assign their final model to an arbitrary
    variable (consistently ``r`` across a 200-sample check of the source
    dataset), but MetaForge's ``cadquery.execute_script`` contract requires
    the final variable be literally named ``result``. This appends a bridge
    line rather than touching the CAD logic itself -- the last top-level
    assignment in the script is taken as "the final model", matching every
    sample inspected while building this suite.
    """
    names = _ASSIGN_RE.findall(script)
    if not names or names[-1] == "result":
        return script
    return script + f"\nresult = {names[-1]}\n"


async def run_one(mcp: Any, sample: dict[str, Any], timeout: int) -> dict[str, Any]:
    script = bridge_to_result(sample["reference_cadquery"])
    start = time.monotonic()
    record: dict[str, Any] = {"id": sample["id"], "model_path": sample.get("model_path")}
    try:
        resp = await mcp.invoke("cadquery.execute_script", {"script": script}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 -- recording the failure IS the eval
        record["executable"] = False
        record["error"] = str(exc)
        record["duration_s"] = round(time.monotonic() - start, 3)
        return record

    record["executable"] = True
    record["duration_s"] = round(time.monotonic() - start, 3)
    record["volume_mm3"] = resp.get("volume_mm3")
    record["surface_area_mm2"] = resp.get("surface_area_mm2")
    record["bounding_box"] = resp.get("bounding_box")
    return record


async def main_async(sample_path: Path, out_path: Path, timeout: int) -> dict[str, Any]:
    from skill_registry.registry_bridge import RegistryMcpBridge
    from tool_registry.bootstrap import bootstrap_tool_registry

    samples = json.loads(sample_path.read_text())
    registry = await bootstrap_tool_registry()
    mcp = RegistryMcpBridge(registry)

    if not await mcp.is_available("cadquery.execute_script"):
        raise RuntimeError(
            "cadquery.execute_script is not available -- run this inside the "
            "gateway container with the cadquery-adapter reachable"
        )

    results = []
    for sample in samples:
        record = await run_one(mcp, sample, timeout)
        results.append(record)
        status = "OK" if record["executable"] else "FAIL"
        print(f"[{status}] {record['id']}", flush=True)

    executable_count = sum(1 for r in results if r["executable"])
    report = {
        "suite": "cad_bench_sandbox_compat",
        "dataset": "CAD-Coder validation (huggingface.co/datasets/gudo7208/CAD-Coder)",
        "what_this_measures": (
            "Sandbox-compatibility only: does MetaForge's cadquery.execute_script "
            "sandbox correctly execute REAL, externally-authored CadQuery scripts? "
            "This does NOT test MetaForge's own text-to-CAD generation quality -- "
            "see evals/cad_bench/README.md for phase 2+."
        ),
        "sample_count": len(samples),
        "executable_count": executable_count,
        "executable_rate": round(executable_count / len(samples), 3) if samples else 0.0,
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print()
    print(f"executable_rate: {report['executable_rate']} ({executable_count}/{len(samples)})")
    print(f"report written to {out_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    asyncio.run(main_async(args.sample, args.out, args.timeout))


if __name__ == "__main__":
    main()
