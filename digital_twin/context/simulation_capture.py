"""Simulation parameter capture + similar-run lookup (MET-331).

Records the exact inputs of every simulation run so the next agent that
asks "what happened last time we ran this?" gets a real answer instead
of re-deriving the parameters from scratch.

Two responsibilities, kept on the digital_twin side of the layer line:

1. **Capture** — accept a ``SimulationParams`` + ``SimulationResult``
   pair from a caller (typically a ``run_fea`` / ``run_cfd`` skill in
   ``domain_agents/simulation/``), persist as a ``SimulationRun``
   record, and optionally publish a ``SESSION`` knowledge entry so the
   summary becomes searchable via ``KnowledgeService``.
2. **Similarity** — answer ``find_similar(params, top_k)`` against the
   captured set. Distance is a simple structural score (solver + mesh
   element-count delta + material overlap + boundary-condition fingerprint
   match) — cheap, deterministic, and good enough for "find runs that
   look like this one." Vector embeddings are deferred to a follow-up
   when the corpus grows.

Layer note: ``digital_twin/`` may not import from ``tool_registry`` /
``domain_agents``, so this module never invokes CalculiX or FreeCAD
directly. Callers in those layers wrap their tool invocation and pass
the captured payload here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from digital_twin.knowledge.service import KnowledgeService
from digital_twin.knowledge.types import KnowledgeType
from observability.tracing import get_tracer

__all__ = [
    "SimulationCapture",
    "SimilarRun",
    "SimulationParams",
    "SimulationResult",
    "SimulationRun",
    "SimulationStatus",
]

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.context.simulation_capture")


class SimulationStatus(BaseModel):
    """Outcome bucket — kept flat so ``record_run`` can construct from a string."""

    value: str = Field(..., description="`success`, `failed`, `cancelled`, `partial`")


class SimulationParams(BaseModel):
    """Inputs that determine a simulation's behaviour.

    Field set is the union of FEA + CFD + thermal essentials. Solver-
    specific extras land in ``solver_options`` so we never reject a run
    because we couldn't model a niche parameter.
    """

    solver: str = Field(..., min_length=1, description="`calculix`, `openfoam`, `elmer`, etc.")
    simulation_type: str = Field(..., description="`fea`, `cfd`, `thermal`, `modal`, ...")
    mesh_element_count: int = Field(0, ge=0)
    mesh_element_type: str | None = Field(default=None, description="`tet10`, `hex8`, ...")
    materials: list[str] = Field(default_factory=list, description="Material names in order of use")
    boundary_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One dict per BC; keys vary by solver but kept verbatim for replay.",
    )
    load_cases: list[dict[str, Any]] = Field(default_factory=list)
    solver_options: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable hash of the inputs — same params → same fingerprint.

        Round-trips through canonical JSON so two callers that supply
        equivalent dicts always collide. SHA-1 is plenty for collision
        avoidance at the < 10⁵ runs scale we expect inside one project.
        """
        payload = json.dumps(
            {
                "solver": self.solver,
                "simulation_type": self.simulation_type,
                "mesh_element_count": self.mesh_element_count,
                "mesh_element_type": self.mesh_element_type,
                "materials": sorted(self.materials),
                "boundary_conditions": _canonical(self.boundary_conditions),
                "load_cases": _canonical(self.load_cases),
                "solver_options": _canonical(self.solver_options),
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


class SimulationResult(BaseModel):
    """Outputs the solver returned. Free-form so any solver can fit."""

    status: str = Field(..., description="`success`, `failed`, `cancelled`, `partial`")
    duration_seconds: float = Field(0.0, ge=0)
    max_stress: float | None = Field(default=None, description="MPa, FEA")
    max_displacement: float | None = Field(default=None, description="mm, FEA")
    max_temperature: float | None = Field(default=None, description="°C, thermal/CFD")
    converged: bool | None = Field(default=None)
    iterations: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimulationRun(BaseModel):
    """One captured simulation invocation — params + result + lineage."""

    id: UUID = Field(default_factory=uuid4)
    cad_model_id: UUID | None = Field(default=None, description="Source CAD work_product")
    params: SimulationParams
    result: SimulationResult
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    summary: str = Field(default="", description="Human-readable one-liner for session knowledge")

    @property
    def fingerprint(self) -> str:
        return self.params.fingerprint()


class SimilarRun(BaseModel):
    """A previous run plus its similarity score to the query params."""

    run: SimulationRun
    distance: float = Field(..., ge=0.0, description="0 = identical params, larger = farther")


class SimulationCapture:
    """In-memory recorder + similarity search for ``SimulationRun`` records.

    A real backend (Postgres + pgvector for embeddings; Neo4j for the
    CAD lineage edge) lands as a follow-up; this in-memory store is
    enough for unit tests and for the gateway to wire today.
    """

    def __init__(self, knowledge_service: KnowledgeService | None = None) -> None:
        self._runs: list[SimulationRun] = []
        self._knowledge_service = knowledge_service

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    async def record_run(
        self,
        params: SimulationParams,
        result: SimulationResult,
        cad_model_id: UUID | None = None,
        summary: str | None = None,
    ) -> SimulationRun:
        """Persist a run; push a session-knowledge entry when wired."""
        with tracer.start_as_current_span("simulation.record_run") as span:
            # Capture both timestamps in order so completed_at >= started_at
            # holds even when the call is sub-microsecond fast.
            started = datetime.now(UTC)
            completed = datetime.now(UTC)
            run = SimulationRun(
                cad_model_id=cad_model_id,
                params=params,
                result=result,
                started_at=started,
                completed_at=completed,
                summary=summary or self._default_summary(params, result, cad_model_id),
            )
            self._runs.append(run)
            span.set_attribute("simulation.run_id", str(run.id))
            span.set_attribute("simulation.solver", params.solver)
            span.set_attribute("simulation.fingerprint", run.fingerprint)

            if self._knowledge_service is not None:
                # Publish a session-summary so the next ContextAssembler
                # search can surface "we already ran this" knowledge.
                await self._publish_session_summary(run)

            logger.info(
                "simulation_recorded",
                run_id=str(run.id),
                solver=params.solver,
                simulation_type=params.simulation_type,
                status=result.status,
                duration_seconds=result.duration_seconds,
                cad_model_id=str(cad_model_id) if cad_model_id else None,
            )
            return run

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def find_similar(
        self,
        params: SimulationParams,
        top_k: int = 5,
        cad_model_id: UUID | None = None,
    ) -> list[SimilarRun]:
        """Return the ``top_k`` previously-captured runs closest to ``params``.

        Distance components (additive — lower is better):

        * Solver mismatch → +1.0
        * simulation_type mismatch → +1.0
        * mesh element-count delta → ``abs(a-b) / max(a,b,1)`` (∈ [0,1])
        * Material set Jaccard distance → ``1 - |∩| / |∪|`` (∈ [0,1])
        * BC topology fingerprint mismatch → +0.5
        * cad_model_id mismatch (when caller supplies one) → +1.0

        Ties broken by recency (newer first).
        """
        candidates: list[tuple[float, SimulationRun]] = []
        target_bc_fp = _bc_fingerprint(params.boundary_conditions)
        target_materials = set(params.materials)

        for run in self._runs:
            distance = 0.0
            if run.params.solver != params.solver:
                distance += 1.0
            if run.params.simulation_type != params.simulation_type:
                distance += 1.0

            denom = max(run.params.mesh_element_count, params.mesh_element_count, 1)
            mesh_delta = abs(run.params.mesh_element_count - params.mesh_element_count) / denom
            distance += mesh_delta

            run_materials = set(run.params.materials)
            union = run_materials | target_materials
            if union:
                inter = run_materials & target_materials
                distance += 1.0 - (len(inter) / len(union))

            if _bc_fingerprint(run.params.boundary_conditions) != target_bc_fp:
                distance += 0.5

            if cad_model_id is not None and run.cad_model_id != cad_model_id:
                distance += 1.0

            candidates.append((distance, run))

        candidates.sort(key=lambda pair: (pair[0], -pair[1].started_at.timestamp()))
        return [SimilarRun(run=run, distance=d) for d, run in candidates[:top_k]]

    def all_runs(self) -> list[SimulationRun]:
        """Read-only snapshot of every captured run."""
        return list(self._runs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_summary(
        params: SimulationParams, result: SimulationResult, cad_model_id: UUID | None
    ) -> str:
        cad = f" on cad/{cad_model_id}" if cad_model_id else ""
        outcome = result.status.upper()
        # Lead with the solver + sim_type so a vector search on
        # "stress simulation" or "calculix" finds the row.
        bits = [
            f"{params.simulation_type.upper()} via {params.solver}{cad} → {outcome}",
            f"  duration: {result.duration_seconds:.1f}s",
        ]
        if result.max_stress is not None:
            bits.append(f"  max_stress: {result.max_stress:.2f} MPa")
        if result.max_displacement is not None:
            bits.append(f"  max_displacement: {result.max_displacement:.3f} mm")
        if result.max_temperature is not None:
            bits.append(f"  max_temperature: {result.max_temperature:.1f} °C")
        if params.materials:
            bits.append(f"  materials: {', '.join(params.materials)}")
        if params.mesh_element_count:
            mesh_kind = params.mesh_element_type or "elements"
            bits.append(f"  mesh: {params.mesh_element_count} {mesh_kind}")
        return "\n".join(bits)

    async def _publish_session_summary(self, run: SimulationRun) -> None:
        if self._knowledge_service is None:
            return
        try:
            await self._knowledge_service.ingest(
                content=run.summary,
                source_path=f"simulation_run://{run.id}",
                knowledge_type=KnowledgeType.SESSION,
                source_work_product_id=run.cad_model_id,
                metadata={
                    "simulation_run_id": str(run.id),
                    "solver": run.params.solver,
                    "simulation_type": run.params.simulation_type,
                    "fingerprint": run.fingerprint,
                    "status": run.result.status,
                    "cad_model_id": str(run.cad_model_id) if run.cad_model_id else None,
                },
            )
        except Exception as exc:  # noqa: BLE001 — knowledge push is best-effort
            logger.warning(
                "simulation_session_publish_failed",
                run_id=str(run.id),
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Sync convenience
    # ------------------------------------------------------------------

    def record_run_sync(
        self,
        params: SimulationParams,
        result: SimulationResult,
        cad_model_id: UUID | None = None,
        summary: str | None = None,
    ) -> SimulationRun:
        """Blocking wrapper for non-async callers (CLI, scripts)."""
        return asyncio.run(self.record_run(params, result, cad_model_id, summary))


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    """Recursively sort dict keys so dict ordering doesn't perturb hashes."""
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _bc_fingerprint(bcs: Iterable[dict[str, Any]]) -> str:
    """Topology hash of a boundary-condition list — order-insensitive."""
    items = sorted(json.dumps(_canonical(bc), sort_keys=True) for bc in bcs)
    return hashlib.sha1("|".join(items).encode("utf-8"), usedforsecurity=False).hexdigest()
