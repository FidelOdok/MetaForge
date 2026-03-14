"""CalculiX MCP server -- FastAPI-based HTTP server exposing FEA tools.

This module runs inside a Docker container and provides an HTTP interface
for the CalculiX solver. It bridges the MCP protocol to the actual ccx binary.

Tools exposed:
    - POST /tools/calculix.run_fea      -- Run FEA and return parsed results
    - POST /tools/calculix.extract_results -- Parse existing .frd result files
    - GET  /health                       -- Health check
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from tool_registry.tools.calculix.result_parser import (
    FrdParseError,
    extract_results,
)
from tool_registry.tools.calculix.solver import (
    MAX_SOLVER_TIMEOUT,
    SolverError,
    SolverTimeoutError,
    run_fea,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.calculix.mcp_server")

# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class RunFeaRequest(BaseModel):
    """Request body for calculix.run_fea tool."""

    mesh_file: str = Field(..., description="Path to .inp mesh file")
    load_case: str = Field(..., description="Load case identifier")
    analysis_type: str = Field(
        default="static_stress",
        description="Type of analysis (static_stress, modal)",
    )
    timeout: int = Field(
        default=MAX_SOLVER_TIMEOUT,
        ge=1,
        le=MAX_SOLVER_TIMEOUT,
        description="Solver timeout in seconds",
    )


class ExtractResultsRequest(BaseModel):
    """Request body for calculix.extract_results tool."""

    frd_path: str = Field(..., description="Path to .frd result file")
    include_node_data: bool = Field(
        default=True,
        description="Include per-node data in results",
    )


class ToolResponse(BaseModel):
    """Standard MCP tool response wrapper."""

    tool_id: str
    status: str  # "success" or "error"
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0
    output_files: list[str] = Field(default_factory=list)
    error: str | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    adapter_id: str = "calculix"
    status: str = "healthy"
    version: str = "0.1.0"
    tools_available: int = 2
    ccx_available: bool = False
    work_dir: str = ""


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CalculiX MCP Adapter",
    description="MCP server for CalculiX FEA solver",
    version="0.1.0",
)

# Configuration from environment
CCX_BINARY = os.environ.get("CCX_BINARY", "ccx")
WORK_DIR = os.environ.get("CALCULIX_WORK_DIR", "/workspace")


@app.post("/tools/calculix.run_fea", response_model=ToolResponse)
async def handle_run_fea(request: RunFeaRequest) -> ToolResponse:
    """Run CalculiX FEA analysis and return parsed results."""
    with tracer.start_as_current_span("mcp.calculix.run_fea") as span:
        span.set_attribute("tool.name", "calculix.run_fea")
        span.set_attribute("calculix.mesh_file", request.mesh_file)
        span.set_attribute("calculix.analysis_type", request.analysis_type)

        start_time = time.monotonic()

        try:
            # Run the solver
            solver_result = await run_fea(
                mesh_file=request.mesh_file,
                load_case=request.load_case,
                analysis_type=request.analysis_type,
                timeout=request.timeout,
                ccx_binary=CCX_BINARY,
                work_dir=WORK_DIR,
            )

            # Parse results from .frd file if available
            parsed: dict[str, Any] = {}
            frd_files = [f for f in solver_result.get("result_files", []) if f.endswith(".frd")]
            if frd_files:
                parsed = extract_results(frd_files[0], include_node_data=True)

            elapsed_ms = (time.monotonic() - start_time) * 1000

            return ToolResponse(
                tool_id="calculix.run_fea",
                status="success",
                data={
                    "solver": {
                        "solver_time_s": solver_result["solver_time_s"],
                        "returncode": solver_result["returncode"],
                    },
                    "results": parsed,
                },
                duration_ms=round(elapsed_ms, 1),
                output_files=solver_result.get("result_files", []),
            )

        except SolverTimeoutError as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            span.record_exception(exc)
            logger.error("Solver timed out", error=str(exc))
            return ToolResponse(
                tool_id="calculix.run_fea",
                status="error",
                data={"error_type": "timeout"},
                duration_ms=round(elapsed_ms, 1),
                error=str(exc),
            )

        except SolverError as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            span.record_exception(exc)
            logger.error("Solver failed", error=str(exc), returncode=exc.returncode)
            return ToolResponse(
                tool_id="calculix.run_fea",
                status="error",
                data={"error_type": "solver_error", "returncode": exc.returncode},
                duration_ms=round(elapsed_ms, 1),
                error=str(exc),
            )

        except (FileNotFoundError, ValueError) as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            span.record_exception(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tools/calculix.extract_results", response_model=ToolResponse)
async def handle_extract_results(request: ExtractResultsRequest) -> ToolResponse:
    """Parse existing CalculiX .frd result files."""
    with tracer.start_as_current_span("mcp.calculix.extract_results") as span:
        span.set_attribute("tool.name", "calculix.extract_results")
        span.set_attribute("calculix.frd_path", request.frd_path)

        start_time = time.monotonic()

        try:
            parsed = extract_results(
                frd_path=request.frd_path,
                include_node_data=request.include_node_data,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000

            return ToolResponse(
                tool_id="calculix.extract_results",
                status="success",
                data=parsed,
                duration_ms=round(elapsed_ms, 1),
                output_files=[request.frd_path],
            )

        except FileNotFoundError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        except FrdParseError as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            span.record_exception(exc)
            return ToolResponse(
                tool_id="calculix.extract_results",
                status="error",
                data={"error_type": "parse_error"},
                duration_ms=round(elapsed_ms, 1),
                error=str(exc),
            )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    import shutil

    ccx_available = shutil.which(CCX_BINARY) is not None
    work_dir_exists = Path(WORK_DIR).exists()

    status = "healthy" if ccx_available and work_dir_exists else "degraded"

    return HealthResponse(
        status=status,
        ccx_available=ccx_available,
        work_dir=WORK_DIR,
    )


def main() -> None:
    """Entry point for the MCP server."""
    host = os.environ.get("MCP_HOST", "0.0.0.0")  # noqa: S104
    port = int(os.environ.get("MCP_PORT", "8200"))

    logger.info("Starting CalculiX MCP server", host=host, port=port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
