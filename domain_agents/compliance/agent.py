"""Compliance domain agent.

Orchestrates compliance checklist generation, evidence tracking,
and regulatory gap analysis for hardware products.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from skill_registry.skill_base import SkillContext

from .checklist_generator import ChecklistGenerator
from .evidence_tracker import EvidenceTracker
from .models import ComplianceChecklist, ComplianceRegime, EvidenceStatus, EvidenceType

logger = structlog.get_logger(__name__)
tracer = get_tracer("compliance.agent")

_DEFAULT_REGIMES_DIR = Path(__file__).resolve().parent / "regimes"


class ComplianceTaskRequest(BaseModel):
    """A request for the compliance agent to perform a task."""

    task_type: str  # "generate_checklist", "link_evidence", "get_coverage"
    project_id: str = ""
    parameters: dict[str, Any] = {}


class ComplianceResult(BaseModel):
    """Result of a compliance agent task."""

    task_type: str
    project_id: str
    success: bool
    total_requirements: int = 0
    evidenced_count: int = 0
    coverage_percent: float = 0.0
    data: dict[str, Any] = {}
    errors: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


class ComplianceAgent:
    """Compliance domain agent.

    Orchestrates compliance checklist generation and evidence tracking.
    Stateless -- all persistent state lives in the Digital Twin.

    Usage::

        agent = ComplianceAgent()
        result = await agent.run_task(ComplianceTaskRequest(
            task_type="generate_checklist",
            project_id="proj-1",
            parameters={"markets": ["UKCA", "CE"]},
        ))
    """

    SUPPORTED_TASKS = {"generate_checklist", "link_evidence", "get_coverage"}

    def __init__(
        self,
        regimes_dir: Path | None = None,
        session_id: UUID | None = None,
    ) -> None:
        self.session_id = session_id or uuid4()
        self._regimes_dir = regimes_dir or _DEFAULT_REGIMES_DIR
        self._generator = ChecklistGenerator()
        self._generator.load_regimes(self._regimes_dir)
        self._tracker = EvidenceTracker()
        self._checklists: dict[str, ComplianceChecklist] = {}
        self.logger = logger.bind(agent="compliance", session_id=str(self.session_id))

    @property
    def generator(self) -> ChecklistGenerator:
        return self._generator

    @property
    def tracker(self) -> EvidenceTracker:
        return self._tracker

    async def run_task(self, request: ComplianceTaskRequest) -> ComplianceResult:
        """Execute a compliance task, routing to the appropriate handler."""
        with tracer.start_as_current_span("compliance_agent.run_task") as span:
            span.set_attribute("task_type", request.task_type)
            span.set_attribute("project_id", request.project_id)

            self.logger.info(
                "Running task",
                task_type=request.task_type,
                project_id=request.project_id,
            )

            if request.task_type not in self.SUPPORTED_TASKS:
                return ComplianceResult(
                    task_type=request.task_type,
                    project_id=request.project_id,
                    success=False,
                    errors=[
                        f"Unsupported task type: {request.task_type}. "
                        f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                    ],
                )

            handler = {
                "generate_checklist": self._handle_generate_checklist,
                "link_evidence": self._handle_link_evidence,
                "get_coverage": self._handle_get_coverage,
            }[request.task_type]

            try:
                return await handler(request)
            except Exception as exc:
                span.record_exception(exc)
                return ComplianceResult(
                    task_type=request.task_type,
                    project_id=request.project_id,
                    success=False,
                    errors=[str(exc)],
                )

    async def _handle_generate_checklist(
        self, request: ComplianceTaskRequest
    ) -> ComplianceResult:
        """Generate a compliance checklist for the project."""
        raw_markets = request.parameters.get("markets", [])
        markets = [ComplianceRegime(m) for m in raw_markets]

        checklist = self._generator.generate_checklist(
            project_id=request.project_id,
            product_category=request.parameters.get(
                "product_category", "consumer_electronics"
            ),
            markets=markets,
        )

        self._checklists[request.project_id] = checklist

        return ComplianceResult(
            task_type=request.task_type,
            project_id=request.project_id,
            success=True,
            total_requirements=checklist.total_items,
            evidenced_count=checklist.evidenced_items,
            coverage_percent=checklist.coverage_percent,
            data={"checklist": checklist.model_dump(mode="json")},
        )

    async def _handle_link_evidence(
        self, request: ComplianceTaskRequest
    ) -> ComplianceResult:
        """Link evidence to a checklist item."""
        item_id = request.parameters.get("checklist_item_id", "")
        ev_type_str = request.parameters.get("evidence_type", "TEST_REPORT")
        title = request.parameters.get("title", "")

        if not item_id or not title:
            return ComplianceResult(
                task_type=request.task_type,
                project_id=request.project_id,
                success=False,
                errors=["checklist_item_id and title are required"],
            )

        evidence = self._tracker.link_evidence(
            checklist_item_id=item_id,
            evidence_type=EvidenceType(ev_type_str),
            title=title,
            description=request.parameters.get("description", ""),
        )

        return ComplianceResult(
            task_type=request.task_type,
            project_id=request.project_id,
            success=True,
            data={"evidence": evidence.model_dump(mode="json")},
        )

    async def _handle_get_coverage(
        self, request: ComplianceTaskRequest
    ) -> ComplianceResult:
        """Get coverage statistics for a project's checklist."""
        checklist = self._checklists.get(request.project_id)
        if checklist is None:
            return ComplianceResult(
                task_type=request.task_type,
                project_id=request.project_id,
                success=False,
                errors=[f"No checklist found for project '{request.project_id}'"],
            )

        coverage = self._tracker.get_coverage(checklist)

        return ComplianceResult(
            task_type=request.task_type,
            project_id=request.project_id,
            success=True,
            total_requirements=int(coverage["total_items"]),
            evidenced_count=int(coverage["evidenced_items"]),
            coverage_percent=float(coverage["coverage_percent"]),
            data={"coverage": coverage},
        )
