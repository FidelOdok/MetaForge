"""Artifact storage for the three-agent harness (MET-474, MET-475).

The Planner / Generator / Evaluator cycle produces and consumes
versioned artifacts — ``design_spec.md`` (hardware), ``plan.md`` /
``bom.csv`` / ``schematic_outline.md`` for hardware, plus ``plan.md``
and generated source files for the coding harness.

The acceptance criteria for both tickets call out that artifacts must
**survive session boundaries**. That contract lives here: every
``ArtifactStore`` implementation persists ``(run_id, name) → content``
and surfaces simple put / get / list operations. The in-process /
in-memory variant powers unit tests and dev runs; a follow-up adds
the on-disk variant that backs production runs.

Artifacts are keyed by ``(run_id, name)`` rather than just ``name`` so
parallel runs of the same harness don't trample each other's
``design_spec.md``. ``run_id`` is supplied by the orchestrator at
``ThreeAgentHarness`` construction and matches the run id surfaced in
events / logs / Linear comments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Artifact:
    """One stored artifact (the file the agents wrote)."""

    run_id: str
    name: str
    content: str
    # Monotonically-increasing per ``(run_id, name)`` so the
    # evaluator can pin "I evaluated version 3 of design_spec.md".
    version: int = 1
    # Free-form metadata — e.g. which agent wrote it, what iteration.
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ArtifactStore(Protocol):
    """Versioned (run_id, name) → Artifact store.

    All methods are async so production backends can hit S3 / Postgres
    / Git without blocking the orchestrator loop. The in-memory
    implementation completes synchronously inside the coroutine.
    """

    async def put(
        self,
        run_id: str,
        name: str,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> Artifact: ...

    async def get(self, run_id: str, name: str) -> Artifact | None: ...

    async def list_for_run(self, run_id: str) -> list[Artifact]: ...


class InMemoryArtifactStore:
    """Dict-backed ``ArtifactStore`` for tests and local dev.

    Versioning is per ``(run_id, name)``: each ``put`` increments the
    version counter and returns the new ``Artifact``. ``get`` always
    returns the latest version; the full history is reachable via
    ``list_for_run`` — entries come back in insertion order.
    """

    def __init__(self) -> None:
        self._by_run: dict[str, list[Artifact]] = {}

    async def put(
        self,
        run_id: str,
        name: str,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> Artifact:
        history = self._by_run.setdefault(run_id, [])
        prior_versions = [a for a in history if a.name == name]
        version = (prior_versions[-1].version + 1) if prior_versions else 1
        artifact = Artifact(
            run_id=run_id,
            name=name,
            content=content,
            version=version,
            metadata=dict(metadata or {}),
        )
        history.append(artifact)
        return artifact

    async def get(self, run_id: str, name: str) -> Artifact | None:
        history = self._by_run.get(run_id, [])
        for artifact in reversed(history):
            if artifact.name == name:
                return artifact
        return None

    async def list_for_run(self, run_id: str) -> list[Artifact]:
        return list(self._by_run.get(run_id, []))
