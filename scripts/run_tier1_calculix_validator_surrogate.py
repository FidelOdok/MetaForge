"""Run the Tier-1 ``calculix.*`` UAT scenarios as the
``uat-validator`` subagent would, capturing per-scenario request/response
evidence and writing a markdown report.

This is the validator surrogate path — used when the parent Claude
Code session doesn't have the metaforge MCP server loaded via
``.mcp.json``. The canonical path is ``/uat-cycle12 --tier 1``.

Spawns ``python -m metaforge.mcp --transport stdio`` with
``METAFORGE_ADAPTER_CALCULIX_URL=http://localhost:8200`` so the unified
server routes ``calculix.*`` tool calls to the running
``metaforge-calculix-adapter-1`` Docker container (per the MET-373
fix landed in PR #140).

Walks 5 scenarios in ``tests/uat/scenarios/tier1/calculix.md``.
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
    "calculix.run_fea",
    "calculix.run_thermal",
    "calculix.validate_mesh",
    "calculix.extract_results",
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

    transport = StdioTransport(
        command=[sys.executable, "-m", "metaforge.mcp", "--transport", "stdio"],
        env={
            **os.environ,
            "METAFORGE_ADAPTERS": "calculix",
            "METAFORGE_ADAPTER_CALCULIX_URL": "http://localhost:8200",
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
        # Scenario 1 (preflight): tool/list reports all four calculix tools
        # --------------------------------------------------------------
        scen = "1: tool/list reports all four calculix tools"
        t0 = time.perf_counter()
        list_raw = await transport.send(
            '{"jsonrpc":"2.0","id":"discover","method":"tool/list","params":{}}'
        )
        list_payload = json.loads(list_raw)
        tools = list_payload.get("result", {}).get("tools", [])
        calculix_tool_ids = {t["tool_id"] for t in tools if t["tool_id"].startswith("calculix.")}
        # Build a manifest map for scenario 5 (manifest reachability check)
        calculix_manifests = {
            t["tool_id"]: t for t in tools if t["tool_id"].startswith("calculix.")
        }
        record(
            scen,
            "tool/list",
            {},
            {
                "calculix_tool_count": len(calculix_tool_ids),
                "calculix_tools": sorted(calculix_tool_ids),
            },
            (time.perf_counter() - t0) * 1000,
        )
        then(
            scen,
            "exactly the 4 expected calculix tools registered",
            calculix_tool_ids == EXPECTED_TOOLS,
            f"missing={sorted(EXPECTED_TOOLS - calculix_tool_ids)}; "
            f"extra={sorted(calculix_tool_ids - EXPECTED_TOOLS)}",
        )

        # --------------------------------------------------------------
        # Scenario 2: validate_mesh accepts an empty / synthetic mesh ref
        # --------------------------------------------------------------
        scen = "2: validate_mesh accepts an empty / synthetic mesh ref"
        s2_args = {"mesh_path": "/tmp/does-not-exist.msh"}
        t0 = time.perf_counter()
        s2_resp = await call("calculix.validate_mesh", s2_args)
        record(
            scen,
            "calculix.validate_mesh (invalid path)",
            s2_args,
            s2_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s2_result = s2_resp.get("result", {}) if isinstance(s2_resp, dict) else {}
        s2_error = s2_resp.get("error", {}) if isinstance(s2_resp, dict) else {}
        s2_status = s2_result.get("status")
        # Either a tool error OR an explicit "failure" status is acceptable.
        is_error_or_failure = bool(s2_error) or s2_status == "failure"
        then(
            scen,
            "validate_mesh on missing file returns error or failure",
            is_error_or_failure,
            f"status={s2_status!r} error={s2_error.get('message')!r}",
        )
        s2_message = (
            (s2_error.get("data", {}) or {}).get("details", "")
            or s2_error.get("message", "")
            or json.dumps(s2_result.get("data", {}))
            or ""
        ).lower()
        then(
            scen,
            "error references the missing/unreadable file",
            any(
                token in s2_message
                for token in (
                    "not found",
                    "missing",
                    "no such",
                    "unreadable",
                    "does not exist",
                    ".msh",
                    "file",
                )
            ),
            f"message={s2_message[:200]!r}",
        )

        # Verify transport stays alive — next tool/list still works
        list2_raw = await transport.send(
            '{"jsonrpc":"2.0","id":"alive","method":"tool/list","params":{}}'
        )
        list2_payload = json.loads(list2_raw)
        then(
            scen,
            "MCP transport stays alive after error",
            len(list2_payload.get("result", {}).get("tools", [])) > 0,
            f"tools_after_error={len(list2_payload.get('result', {}).get('tools', []))}",
        )

        # --------------------------------------------------------------
        # Scenario 3: run_fea rejects missing required arguments cleanly
        # --------------------------------------------------------------
        scen = "3: run_fea rejects missing required arguments cleanly"
        t0 = time.perf_counter()
        s3_resp = await call("calculix.run_fea", {})
        record(
            scen,
            "calculix.run_fea (empty args)",
            {},
            s3_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s3_error = s3_resp.get("error", {}) if isinstance(s3_resp, dict) else {}
        s3_result = s3_resp.get("result", {}) if isinstance(s3_resp, dict) else {}
        s3_status = s3_result.get("status")
        is_error_or_failure = bool(s3_error) or s3_status == "failure"
        then(
            scen,
            "run_fea on empty args returns error or failure",
            is_error_or_failure,
            f"status={s3_status!r} error={s3_error.get('message')!r}",
        )
        s3_message = (
            (s3_error.get("data", {}) or {}).get("details", "")
            or s3_error.get("message", "")
            or json.dumps(s3_result.get("data", {}))
            or ""
        ).lower()
        # The error should name at least one missing required field.
        # Per the manifest: mesh, material, boundary conditions, load cases.
        named_missing = any(
            token in s3_message
            for token in (
                "mesh",
                "material",
                "boundary",
                "load",
                "required",
                "missing",
                "validation",
            )
        )
        then(
            scen,
            "error names at least one missing required field",
            named_missing,
            f"message={s3_message[:200]!r}",
        )

        # --------------------------------------------------------------
        # Scenario 4: extract_results on a non-existent run id fails cleanly
        # --------------------------------------------------------------
        scen = "4: extract_results on non-existent run id fails cleanly"
        s4_args = {"run_id": "uat-nonexistent-9z"}
        t0 = time.perf_counter()
        s4_resp = await call("calculix.extract_results", s4_args)
        record(
            scen,
            "calculix.extract_results (bogus run id)",
            s4_args,
            s4_resp,
            (time.perf_counter() - t0) * 1000,
        )
        s4_error = s4_resp.get("error", {}) if isinstance(s4_resp, dict) else {}
        s4_result = s4_resp.get("result", {}) if isinstance(s4_resp, dict) else {}
        s4_status = s4_result.get("status")
        is_error_or_failure = bool(s4_error) or s4_status == "failure"
        then(
            scen,
            "extract_results on bogus run_id returns error or failure",
            is_error_or_failure,
            f"status={s4_status!r} error={s4_error.get('message')!r}",
        )
        s4_message = (
            (s4_error.get("data", {}) or {}).get("details", "")
            or s4_error.get("message", "")
            or json.dumps(s4_result.get("data", {}))
            or ""
        )
        # Error should NOT leak filesystem paths from inside the
        # container. Look for /var, /workspace, /tmp/calculix internals.
        leaks_internal_path = any(
            internal in s4_message
            for internal in ("/var/lib/", "/workspace/private/", "/etc/calculix/", "/root/")
        )
        then(
            scen,
            "error does not leak filesystem paths from container",
            not leaks_internal_path,
            f"message_excerpt={s4_message[:200]!r}",
        )

        # --------------------------------------------------------------
        # Scenario 5: run_thermal manifest is reachable
        # --------------------------------------------------------------
        scen = "5: run_thermal manifest is reachable"
        thermal_manifest = calculix_manifests.get("calculix.run_thermal", {})
        record(
            scen,
            "manifest cache check (no MCP call)",
            {},
            {
                "description_len": len(thermal_manifest.get("description", "")),
                "input_schema_property_count": len(
                    (thermal_manifest.get("input_schema") or {}).get("properties", {})
                ),
            },
            0.0,
        )
        then(
            scen,
            "run_thermal manifest has non-empty description",
            bool(thermal_manifest.get("description", "").strip()),
            f"description={thermal_manifest.get('description', '')[:200]!r}",
        )
        thermal_props = (thermal_manifest.get("input_schema") or {}).get("properties", {})
        then(
            scen,
            "run_thermal manifest declares ≥1 input_schema property",
            len(thermal_props) >= 1,
            f"property_count={len(thermal_props)} keys={list(thermal_props.keys())[:6]}",
        )

    finally:
        await transport.disconnect()

    elapsed = time.perf_counter() - start_total
    output = {
        "scenario_set": "tests/uat/scenarios/tier1/calculix.md",
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
