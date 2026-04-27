"""Run the Tier-0 golden-flow scenario as the uat-validator would,
capturing per-step request/response evidence, then writing a report
mimicking the subagent output format.

This is the validator surrogate path — used when the parent Claude
Code session doesn't have the metaforge MCP server loaded via
.mcp.json (e.g. session was started before PR #132 landed). The
canonical path is `/uat-cycle12` which spawns the subagent with
mcp__metaforge__* tools available.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path("/mnt/c/Users/odokf/Documents/MetaForge")
sys.path.insert(0, str(REPO_ROOT))


async def main() -> int:
    import uuid

    from digital_twin.knowledge import create_knowledge_service
    from digital_twin.knowledge.types import KnowledgeType
    from mcp_core.client import McpClient
    from mcp_core.schemas import ToolManifest
    from mcp_core.transports import StdioTransport

    evidence: list[dict] = []
    verdicts: list[dict] = []
    start_total = time.perf_counter()
    overall_status = "PASS"

    def record(step: str, tool: str, request: dict, response: dict | str, duration_ms: float):
        evidence.append(
            {
                "step": step,
                "tool": tool,
                "request": request,
                "response": response,
                "duration_ms": round(duration_ms, 1),
            }
        )

    def then(label: str, condition: bool, detail: str = ""):
        nonlocal overall_status
        verdict = "PASS" if condition else "FAIL"
        if not condition:
            overall_status = "FAIL"
        verdicts.append({"then": label, "verdict": verdict, "detail": detail})

    # ------------------------------------------------------------------
    # GIVEN: fresh KnowledgeService
    # ------------------------------------------------------------------
    svc_suffix = uuid.uuid4().hex[:8]
    svc = create_knowledge_service(
        "lightrag",
        working_dir=f"/tmp/lightrag-tier0-validator-{svc_suffix}",
        postgres_dsn="postgresql://metaforge:metaforge@localhost:5432/metaforge",
        namespace_prefix=f"lightrag_tier0_{svc_suffix}",
    )
    await svc.initialize()
    try:
        # ----- Step 1: knowledge.ingest -----
        ingest_request = {
            "content": (
                "MetaForge UAT Tier-0 marker phrase. The SR-7 bracket is "
                "fabricated from titanium grade 5; the previous aluminium "
                "6061 prototype failed thermal-cycle testing."
            ),
            "source_path": "uat://tier0/sr7-bracket.md",
            "knowledge_type": "design_decision",
        }
        t0 = time.perf_counter()
        ingest_result = await svc.ingest(
            content=ingest_request["content"],
            source_path=ingest_request["source_path"],
            knowledge_type=KnowledgeType.DESIGN_DECISION,
        )
        record(
            "1: knowledge.ingest",
            "knowledge.ingest",
            ingest_request,
            {
                "chunks_indexed": ingest_result.chunks_indexed,
                "source_path": ingest_result.source_path,
                "entry_ids": [str(e) for e in ingest_result.entry_ids][:3],
            },
            (time.perf_counter() - t0) * 1000,
        )

        # ----- Step 2: knowledge.search -----
        search_request = {
            "query": "What material does the SR-7 bracket use?",
            "top_k": 3,
        }
        t0 = time.perf_counter()
        hits = await svc.search(query=search_request["query"], top_k=search_request["top_k"])
        record(
            "2: knowledge.search",
            "knowledge.search",
            search_request,
            {
                "hit_count": len(hits),
                "hits": [
                    {
                        "source_path": h.source_path,
                        "similarity_score": round(h.similarity_score, 3),
                        "content_preview": (h.content or "")[:120],
                    }
                    for h in hits
                ],
            },
            (time.perf_counter() - t0) * 1000,
        )

        # Assertions for steps 1 & 2
        then(
            "Step 1 returns chunks_indexed >= 1",
            ingest_result.chunks_indexed >= 1,
            f"chunks_indexed={ingest_result.chunks_indexed}",
        )
        then(
            "Step 2 returns ≥1 hit whose source_path equals 'uat://tier0/sr7-bracket.md'",
            any(h.source_path == "uat://tier0/sr7-bracket.md" for h in hits),
            f"top hits: {[h.source_path for h in hits]}",
        )
        then(
            "Step 2's top hit's content mentions 'titanium grade 5'",
            len(hits) > 0 and "titanium grade 5" in (hits[0].content or "").lower(),
            f"top content preview: {(hits[0].content or '')[:200] if hits else 'NO HITS'!r}",
        )
    finally:
        await svc.close()

    # ----- Step 3: cadquery.create_parametric -----
    # Spawn metaforge.mcp subprocess and call the tool through it
    transport = StdioTransport(
        command=[sys.executable, "-m", "metaforge.mcp", "--transport", "stdio"],
        env={
            **__import__("os").environ,
            "METAFORGE_ADAPTERS": "cadquery",
        },
        ready_signal="metaforge-mcp ready",
        ready_timeout=30.0,
    )
    await transport.connect()
    client = McpClient()
    await client.connect("metaforge", transport)
    # discover
    raw = await transport.send('{"jsonrpc":"2.0","id":"d","method":"tool/list","params":{}}')
    payload = json.loads(raw)
    for tool in payload.get("result", {}).get("tools", []):
        client.register_manifest(
            ToolManifest(
                tool_id=tool["tool_id"],
                adapter_id=tool.get("adapter_id", "metaforge"),
                name=tool["name"],
                description=tool.get("description", ""),
                capability=tool.get("capability", ""),
                input_schema=tool.get("input_schema", {}),
                output_schema=tool.get("output_schema", {}),
                phase=tool.get("phase", 1),
            )
        )

    cad_request = {
        "shape_type": "box",
        "parameters": {"width": 50, "length": 30, "height": 10},
        "output_path": "/tmp/uat-tier0-bracket.step",
        "material": "titanium grade 5",
    }
    t0 = time.perf_counter()
    try:
        # Use raw JSON-RPC so we route through the unified server's
        # single transport regardless of the per-adapter id in the
        # manifest (the integration-test pattern).
        rpc_request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "cadcall",
                "method": "tool/call",
                "params": {
                    "tool_id": "cadquery.create_parametric",
                    "arguments": cad_request,
                },
            }
        )
        raw_response = await transport.send(rpc_request)
        cad_response = json.loads(raw_response)
    except Exception as exc:
        cad_response = {"error": str(exc), "exception_type": type(exc).__name__}
    finally:
        await client.disconnect("metaforge")

    record(
        "3: cadquery.create_parametric",
        "cadquery.create_parametric",
        cad_request,
        cad_response,
        (time.perf_counter() - t0) * 1000,
    )

    # Raw JSON-RPC response shape: {"result": {"status": ..., "data": ...}} OR
    # {"error": {"code": ..., "message": ..., "data": {...}}}
    cad_result_block = cad_response.get("result", {}) if isinstance(cad_response, dict) else {}
    cad_error_block = cad_response.get("error", {}) if isinstance(cad_response, dict) else {}
    cad_status = cad_result_block.get("status") if cad_result_block else None
    cad_file = cad_result_block.get("data", {}).get("cad_file") if cad_result_block else None

    cad_details_str = ""
    if cad_error_block:
        cad_details_raw = cad_error_block.get("data", {}).get("details", "")
        cad_details_str = cad_details_raw[:200] if isinstance(cad_details_raw, str) else ""
    then(
        "Step 3 returns status='success'",
        cad_status == "success",
        f"got status={cad_status!r}; "
        f"error={cad_error_block.get('message', '')!r}; "
        f"details={cad_details_str!r}",
    )
    then(
        "Step 3 returns a non-empty cad_file path",
        bool(cad_file),
        f"cad_file={cad_file!r}",
    )

    elapsed = time.perf_counter() - start_total
    then(
        "Full sequence completes in under 60s",
        elapsed < 60.0,
        f"elapsed={elapsed:.2f}s",
    )

    # Print results as JSON for the calling shell to consume
    output = {
        "scenario": "Claude ingests a doc, searches it, then generates a CAD part",
        "validates": ["MET-337", "MET-346", "MET-293", "MET-335", "MET-336"],
        "tier": 0,
        "verdict": overall_status,
        "evidence": evidence,
        "verdicts": verdicts,
        "elapsed_seconds": round(elapsed, 2),
    }
    print(json.dumps(output, indent=2, default=str))
    return 0 if overall_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
