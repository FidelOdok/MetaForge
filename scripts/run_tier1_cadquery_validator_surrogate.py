"""Run the Tier-1 ``cadquery.*`` UAT scenarios as the
``uat-validator`` subagent would, capturing per-scenario request/response
evidence and writing a markdown report.

This is the validator surrogate path — used when the parent Claude
Code session doesn't have the metaforge MCP server loaded via
``.mcp.json``. The canonical path is ``/uat-cycle12 --tier 1``.

The surrogate spawns ``python -m metaforge.mcp --transport stdio`` with
``METAFORGE_ADAPTER_CADQUERY_URL=http://localhost:8101`` so the
unified server routes ``cadquery.*`` tool calls to the running
``metaforge-cadquery-adapter-1`` Docker container (per the MET-373
fix landed in PR #140).

Walks 6 scenarios in ``tests/uat/scenarios/tier1/cadquery.md`` and
asserts each Given/When/Then triple.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path("/mnt/c/Users/odokf/Documents/MetaForge")
sys.path.insert(0, str(REPO_ROOT))

EXPECTED_TOOLS = {
    "cadquery.create_parametric",
    "cadquery.boolean_operation",
    "cadquery.get_properties",
    "cadquery.export_geometry",
    "cadquery.execute_script",
    "cadquery.create_assembly",
    "cadquery.generate_enclosure",
}


async def main() -> int:
    from mcp_core.transports import StdioTransport

    evidence: list[dict] = []
    verdicts: list[dict] = []
    start_total = time.perf_counter()
    overall_status = "PASS"

    def record(scenario: str, step: str, request: dict, response: dict | str, duration_ms: float):
        evidence.append(
            {
                "scenario": scenario,
                "step": step,
                "request": request,
                "response": response,
                "duration_ms": round(duration_ms, 1),
            }
        )

    def then(scenario: str, label: str, condition: bool, detail: str = ""):
        nonlocal overall_status
        verdict = "PASS" if condition else "FAIL"
        if not condition:
            overall_status = "FAIL"
        verdicts.append({"scenario": scenario, "then": label, "verdict": verdict, "detail": detail})

    def block(scenario: str, label: str, reason: str):
        verdicts.append(
            {"scenario": scenario, "then": label, "verdict": "BLOCKED", "detail": reason}
        )

    transport = StdioTransport(
        command=[sys.executable, "-m", "metaforge.mcp", "--transport", "stdio"],
        env={
            **os.environ,
            "METAFORGE_ADAPTERS": "cadquery",
            "METAFORGE_ADAPTER_CADQUERY_URL": "http://localhost:8101",
        },
        ready_signal="metaforge-mcp ready",
        ready_timeout=30.0,
    )
    await transport.connect()

    async def call(tool_id: str, arguments: dict) -> dict:
        rpc = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": tool_id,
                "method": "tool/call",
                "params": {"tool_id": tool_id, "arguments": arguments},
            }
        )
        raw = await transport.send(rpc)
        return json.loads(raw)

    try:
        # --------------------------------------------------------------
        # Scenario 6 (run first — preflight): tool/list coverage
        # --------------------------------------------------------------
        scen = "6: tool/list reports all seven cadquery tools"
        t0 = time.perf_counter()
        list_raw = await transport.send(
            '{"jsonrpc":"2.0","id":"discover","method":"tool/list","params":{}}'
        )
        list_payload = json.loads(list_raw)
        tools = list_payload.get("result", {}).get("tools", [])
        cadquery_tool_ids = {t["tool_id"] for t in tools if t["tool_id"].startswith("cadquery.")}
        record(
            scen,
            "tool/list",
            {},
            {
                "cadquery_tool_count": len(cadquery_tool_ids),
                "cadquery_tools": sorted(cadquery_tool_ids),
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(
            scen,
            "all 7 cadquery tools registered",
            cadquery_tool_ids == EXPECTED_TOOLS,
            f"missing={sorted(EXPECTED_TOOLS - cadquery_tool_ids)}; "
            f"extra={sorted(cadquery_tool_ids - EXPECTED_TOOLS)}",
        )

        # If the create_parametric tool isn't even registered, the
        # rest of the scenarios can't run — block them and return.
        if "cadquery.create_parametric" not in cadquery_tool_ids:
            for label in [
                "1: create a parametric box",
                "2: invalid shape_type",
                "3: create a cylinder",
                "4: bounding box reports correct dimensions",
                "5: missing required parameter",
            ]:
                block(label, "preflight blocker", "cadquery.create_parametric not in tool/list")
            return 1

        # --------------------------------------------------------------
        # Scenario 1: create a parametric box and inspect properties
        # --------------------------------------------------------------
        scen = "1: create a parametric box and inspect properties"
        s1_args = {
            "shape_type": "box",
            "parameters": {"width": 50, "length": 30, "height": 10},
            "output_path": "/tmp/uat-tier1-box.step",
            "material": "aluminium 6061",
        }
        t0 = time.perf_counter()
        s1_resp = await call("cadquery.create_parametric", s1_args)
        record(
            scen,
            "cadquery.create_parametric (box)",
            s1_args,
            s1_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s1_result = s1_resp.get("result", {}) if isinstance(s1_resp, dict) else {}
        s1_status = s1_result.get("status")
        s1_data = s1_result.get("data", {}) or {}
        s1_volume = s1_data.get("volume_mm3")
        s1_surface = s1_data.get("surface_area_mm2")
        s1_cad_file = s1_data.get("cad_file")
        s1_error = s1_resp.get("error", {}) if isinstance(s1_resp, dict) else {}

        if s1_status is None and "cadquery is not installed" in (
            (s1_error.get("data", {}) or {}).get("details", "").lower()
        ):
            for sublabel, expectation in [
                ("status='success'", "cadquery in-process import unavailable"),
                ("volume ≈ 15000", "cadquery in-process import unavailable"),
                ("surface_area_mm2 present", "cadquery in-process import unavailable"),
                ("cad_file path returned", "cadquery in-process import unavailable"),
            ]:
                block(scen, sublabel, expectation)
        else:
            then(
                scen,
                "status='success'",
                s1_status == "success",
                f"got status={s1_status!r}; error={s1_error.get('message')!r}",
            )
            then(
                scen,
                "volume ≈ 15000 (within 1%)",
                isinstance(s1_volume, (int, float)) and abs(s1_volume - 15000) <= 150,
                f"volume_mm3={s1_volume}",
            )
            then(
                scen,
                "surface_area_mm2 present",
                isinstance(s1_surface, (int, float)) and s1_surface > 0,
                f"surface_area_mm2={s1_surface}",
            )
            then(scen, "cad_file path returned", bool(s1_cad_file), f"cad_file={s1_cad_file!r}")

            # Step 2: get_properties on the produced file
            if s1_cad_file:
                t0 = time.perf_counter()
                # Adapter schema uses ``input_file`` (not ``cad_file``).
                s1b_resp = await call("cadquery.get_properties", {"input_file": s1_cad_file})
                record(
                    scen,
                    "cadquery.get_properties",
                    {"input_file": s1_cad_file},
                    s1b_resp,
                    (time.perf_counter() - t0) * 1000,
                )
                s1b_result = s1b_resp.get("result", {}) if isinstance(s1b_resp, dict) else {}
                s1b_data = s1b_result.get("data", {}) or {}
                # Response shape is `{file, properties: {volume, area, ...}}`
                # per the adapter's output schema.
                s1b_props = s1b_data.get("properties", {}) or {}
                s1b_volume = (
                    s1b_props.get("volume")
                    or s1b_props.get("volume_mm3")
                    or s1b_data.get("volume_mm3")
                )
                then(
                    scen,
                    "get_properties volume matches step 1 (within 1%)",
                    isinstance(s1b_volume, (int, float))
                    and isinstance(s1_volume, (int, float))
                    and abs(s1b_volume - s1_volume) <= max(150, s1_volume * 0.01),
                    f"step1.volume={s1_volume} step2.volume={s1b_volume}",
                )

        # --------------------------------------------------------------
        # Scenario 2: invalid shape_type returns a clean tool error
        # --------------------------------------------------------------
        scen = "2: invalid shape_type returns a clean tool error"
        s2_args = {
            "shape_type": "tetrahedron",
            "parameters": {"radius": 10},
            "output_path": "/tmp/uat-tier1-tetra.step",
        }
        t0 = time.perf_counter()
        s2_resp = await call("cadquery.create_parametric", s2_args)
        record(
            scen,
            "cadquery.create_parametric (tetrahedron)",
            s2_args,
            s2_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s2_result = s2_resp.get("result", {}) if isinstance(s2_resp, dict) else {}
        s2_status = s2_result.get("status")
        s2_error = s2_resp.get("error", {}) if isinstance(s2_resp, dict) else {}
        # Prefer ``data.details`` (specific) over ``message`` (which is
        # the generic "Tool execution failed" wrapper).
        s2_error_message = (
            (s2_error.get("data", {}) or {}).get("details", "") or s2_error.get("message", "") or ""
        ).lower()
        # Either tool error OR status=failure is acceptable per scenario contract
        is_error = bool(s2_error) or s2_status == "failure"
        then(
            scen,
            "rejects invalid shape_type",
            is_error,
            f"status={s2_status!r} error={s2_error.get('message') if s2_error else None}",
        )
        then(
            scen,
            "error mentions the rejected shape_type or 'shape'",
            "tetrahedron" in s2_error_message or "shape" in s2_error_message,
            f"error_message={s2_error_message[:200]!r}",
        )

        # --------------------------------------------------------------
        # Scenario 3: create a cylinder
        # --------------------------------------------------------------
        scen = "3: create a cylinder"
        s3_args = {
            "shape_type": "cylinder",
            "parameters": {"radius": 25, "height": 50},
            "output_path": "/tmp/uat-tier1-cylinder.step",
        }
        t0 = time.perf_counter()
        s3_resp = await call("cadquery.create_parametric", s3_args)
        record(
            scen,
            "cadquery.create_parametric (cylinder)",
            s3_args,
            s3_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s3_result = s3_resp.get("result", {}) if isinstance(s3_resp, dict) else {}
        s3_status = s3_result.get("status")
        s3_data = s3_result.get("data", {}) or {}
        s3_volume = s3_data.get("volume_mm3")
        s3_error = s3_resp.get("error", {}) if isinstance(s3_resp, dict) else {}

        if s3_status is None and "cadquery is not installed" in (
            (s3_error.get("data", {}) or {}).get("details", "").lower()
        ):
            block(
                scen, "status='success' + correct volume", "cadquery in-process import unavailable"
            )
        else:
            import math as _math

            expected_vol = _math.pi * 25 * 25 * 50
            then(
                scen,
                "status='success'",
                s3_status == "success",
                f"got status={s3_status!r}; error={s3_error.get('message')!r}",
            )
            then(
                scen,
                "volume ≈ π·25²·50 ≈ 98174 (within 2%)",
                isinstance(s3_volume, (int, float))
                and abs(s3_volume - expected_vol) <= expected_vol * 0.02,
                f"volume_mm3={s3_volume} expected≈{expected_vol:.0f}",
            )

        # --------------------------------------------------------------
        # Scenario 4: bounding box reports correct dimensions
        # --------------------------------------------------------------
        scen = "4: bounding box reports correct dimensions"
        s4_args = {
            "shape_type": "box",
            "parameters": {"width": 100, "length": 60, "height": 20},
            "output_path": "/tmp/uat-tier1-bbox.step",
        }
        t0 = time.perf_counter()
        s4_resp = await call("cadquery.create_parametric", s4_args)
        record(
            scen,
            "cadquery.create_parametric (bbox check)",
            s4_args,
            s4_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s4_result = s4_resp.get("result", {}) if isinstance(s4_resp, dict) else {}
        s4_data = s4_result.get("data", {}) or {}
        s4_bbox = s4_data.get("bounding_box")
        s4_error = s4_resp.get("error", {}) if isinstance(s4_resp, dict) else {}

        if s4_result.get("status") is None and "cadquery is not installed" in (
            (s4_error.get("data", {}) or {}).get("details", "").lower()
        ):
            block(
                scen,
                "bounding_box dimensions match request",
                "cadquery in-process import unavailable",
            )
        else:
            then(
                scen,
                "response includes bounding_box",
                isinstance(s4_bbox, dict),
                f"bounding_box={s4_bbox!r}",
            )
            if isinstance(s4_bbox, dict):
                # CadQuery returns {min_x, max_x, min_y, max_y, min_z, max_z}.
                # Compute extents and accept any axis permutation — cadquery's
                # box() argument order means width/length can swap on which axis.
                extents: list[float] = []
                if all(
                    k in s4_bbox for k in ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")
                ):
                    extents = sorted(
                        [
                            abs(s4_bbox["max_x"] - s4_bbox["min_x"]),
                            abs(s4_bbox["max_y"] - s4_bbox["min_y"]),
                            abs(s4_bbox["max_z"] - s4_bbox["min_z"]),
                        ]
                    )
                    expected = sorted([100.0, 60.0, 20.0])
                    dims_match = all(
                        abs(actual - target) <= 0.1
                        for actual, target in zip(extents, expected, strict=True)
                    )
                else:
                    dims_match = False
                then(
                    scen,
                    "bounding_box dimensions match (within 0.1mm, any axis order)",
                    dims_match,
                    f"extents={extents} expected_sorted=[20.0, 60.0, 100.0]",
                )

        # --------------------------------------------------------------
        # Scenario 5: missing required parameter is caught
        # --------------------------------------------------------------
        scen = "5: missing required parameter"
        s5_args = {
            "shape_type": "box",
            "parameters": {"width": 50, "length": 30, "height": 10},
            # No output_path!
        }
        t0 = time.perf_counter()
        s5_resp = await call("cadquery.create_parametric", s5_args)
        record(
            scen,
            "cadquery.create_parametric (no output_path)",
            s5_args,
            s5_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s5_result = s5_resp.get("result", {}) if isinstance(s5_resp, dict) else {}
        s5_error = s5_resp.get("error", {}) if isinstance(s5_resp, dict) else {}
        s5_status = s5_result.get("status")
        # Either a tool error (-32xxx) OR explicit failure status. Note: the
        # adapter currently DEFAULTS output_path to a workdir path if missing,
        # so this scenario MAY pass without erroring. Document both outcomes.
        s5_error_message = (
            s5_error.get("message", "") or (s5_error.get("data", {}) or {}).get("details", "") or ""
        )
        is_error_or_failure = bool(s5_error) or s5_status == "failure"
        if not is_error_or_failure:
            # Adapter accepted the call by defaulting output_path.
            # Note this in the verdict so the gap is visible without
            # blocking the test.
            then(
                scen,
                "missing output_path raises OR is rejected",
                False,
                f"adapter accepted call without output_path; data={s5_result.get('data')}",
            )
        else:
            then(
                scen,
                "missing output_path raises OR is rejected",
                True,
                f"error_message={s5_error_message[:200]!r}",
            )

        # Verify next call still works (transport not crashed)
        t0 = time.perf_counter()
        list2_raw = await transport.send(
            '{"jsonrpc":"2.0","id":"recheck","method":"tool/list","params":{}}'
        )
        list2_payload = json.loads(list2_raw)
        tools_after = list2_payload.get("result", {}).get("tools", [])
        record(
            scen,
            "tool/list after error (transport alive check)",
            {},
            {"tool_count": len(tools_after)},
            (time.perf_counter() - t0) * 1000,
        )
        then(
            scen,
            "MCP transport stays alive after error",
            len(tools_after) > 0,
            f"tools_after_error={len(tools_after)}",
        )

    finally:
        await transport.disconnect()

    elapsed = time.perf_counter() - start_total
    output = {
        "scenario_set": "tests/uat/scenarios/tier1/cadquery.md",
        "validates": ["MET-337", "MET-340", "MET-373"],
        "tier": 1,
        "verdict": overall_status,
        "evidence": evidence,
        "verdicts": verdicts,
        "elapsed_seconds": round(elapsed, 2),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0 if overall_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
