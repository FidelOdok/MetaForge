"""RequirementAuthorAgent (FORGE-55, spec section 26.4 Requirement Author
Agent).

Same one-shot structured-JSON LLM pattern as ``IntentInterpreterAgent``
(FORGE-54) and, further back, ``req_handlers.py``'s ``_extract_req_spec``.
Every generated requirement is run through ``RequirementLinter`` BEFORE
being proposed -- its ``RequirementQualityRecord`` and raw lint findings
ride along in the Constraint's ``metadata``, so a generated requirement
that fails quality checks is still returned (never silently dropped), just
visibly flagged rather than presented as clean. Produces a proposed
``Patch`` (Phase 2's model), never a direct write.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.requirement_intelligence.linter import RequirementLinter
from api_gateway.requirement_intelligence.models import AgentResult
from api_gateway.requirement_intelligence.quality import build_quality_record
from twin_core.models.patch import Patch, PatchOp, PatchOperation

logger = structlog.get_logger(__name__)

_PROMPT_TEMPLATE = (
    "You are the Requirement Author for a hardware engineering harness. Generate candidate "
    "engineering requirements from the source statement below. Preserve the source's intent -- "
    "do not invent scope it doesn't support. Reply with ONLY JSON:\n"
    '{{"requirements": [{{"statement": "<one atomic, quantified requirement, using \'shall\'>", '
    '"rationale": "<why this requirement follows from the source>", '
    '"verification_method": "<test | analysis | inspection | demonstration>", '
    '"confidence": 0.0}}]}}\n'
    "Each requirement must be atomic (one testable claim), quantified with a real threshold and "
    'unit where applicable, and use "shall"/"must" rather than "should"/"may". Score '
    "confidence honestly in [0, 1].\n\n"
    "Source statement: {source}\nContext: {context}"
)


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _str(value: Any, cap: int = 500) -> str:
    return str(value).strip()[:cap] if isinstance(value, (str, int, float)) else ""


def _constraint_name(statement: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in statement.strip().lower())
    slug = "_".join(p for p in slug.split("_") if p)
    return (slug or "candidate_requirement")[:60]


class RequirementAuthorAgent:
    """Generates candidate requirements from a source statement, each
    linted before being proposed -- never a committed write."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        invoke: Any = None,
        extract: Any = None,
        linter: RequirementLinter | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._invoke = invoke
        self._extract_fn = extract or self._extract
        self._linter = linter or RequirementLinter()

    async def author(
        self,
        source_statement: str,
        context: str = "",
        *,
        project_id: str | None = None,
        parent_ref: str | None = None,
        relation: str = "derives_from",
    ) -> AgentResult:
        spec = await self._extract_fn(source_statement, context)

        operations: list[PatchOperation] = []
        conclusions: list[str] = []
        confidences: list[float] = []

        for item in spec.get("requirements") or []:
            if not isinstance(item, dict):
                continue
            text = _str(item.get("statement"))
            if not text:
                continue

            entity_id = uuid4()
            findings = self._linter.lint(text)
            # None (not evaluated) when no parent_ref was given at all --
            # distinct from a real has_parent=False the caller would pass
            # from an actual "no parent found" check, which this call site
            # doesn't perform (it only knows whether a parent was named).
            has_parent = None if parent_ref is None else True
            quality = build_quality_record(findings, has_parent=has_parent)
            confidence = _clamp01(item.get("confidence"))
            confidences.append(confidence)
            conclusions.append(text)

            operations.append(
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="constraint",
                    entity={
                        "id": entity_id,
                        "name": _constraint_name(text),
                        # A generated candidate has no evaluable expression
                        # yet -- same INFO/"True" precedent FORGE-54's
                        # candidate constraints and requirement extraction
                        # already use; giving it one is a later, separate
                        # concern (deriving a real Constraint.expression from
                        # the statement is not this agent's job).
                        "expression": "True",
                        "severity": "info",
                        "domain": "candidate",
                        "source": "requirement_author_agent",
                        "message": text,
                        "metadata": {
                            "candidate": True,
                            "rationale": _str(item.get("rationale"), cap=500),
                            "verification_method": _str(item.get("verification_method"), cap=60)
                            or "unspecified",
                            "confidence": confidence,
                            "quality": quality.model_dump(),
                            "lint_findings": [f.model_dump() for f in findings],
                        },
                    },
                )
            )

            if parent_ref:
                operations.append(
                    PatchOperation(
                        op=PatchOp.LINK,
                        entity_id=entity_id,
                        relation=relation,
                        target_id=_parse_uuid(parent_ref),
                    )
                )

        patch = None
        if operations:
            patch = Patch(
                operations=operations,
                reason=f"Requirement Author generation from: {source_statement[:120]}",
                created_by="agent:requirement_author",
                project_id=None if project_id is None else _parse_uuid(project_id),
            )

        confidence = sum(confidences) / len(confidences) if confidences else 0.5
        logger.info(
            "requirement_author_result",
            source=source_statement[:80],
            requirement_count=len(confidences),
            confidence=confidence,
        )
        return AgentResult(
            conclusions=conclusions,
            assumptions=[],
            evidence=[],
            proposed_patch=patch,
            unresolved=[],
            confidence=confidence,
        )

    async def _extract(self, source: str, context: str) -> dict[str, Any]:
        """Ask the LLM for candidate requirements (never raises -- returns {}
        on failure, mirroring req_handlers.py's _extract_req_spec)."""
        from api_gateway.chat.harness_backend import run_chat_turn
        from api_gateway.chat.routes import get_metrics

        prompt = _PROMPT_TEMPLATE.format(source=source, context=context or "(none)")
        try:
            kwargs: dict[str, Any] = {
                "mcp_bridge": None,
                "session_id": "requirement-author",
                "max_steps": 1,
                "provider": self._provider,
                "model": self._model,
                "metrics": get_metrics(),
            }
            if self._invoke is not None:
                kwargs["invoke"] = self._invoke
            reply = await run_chat_turn(prompt, **kwargs)
            match = re.search(r"\{.*\}", reply, re.DOTALL)
            spec = json.loads(match.group(0)) if match else {}
        except Exception as exc:  # noqa: BLE001 - degrade to an empty spec, never fail the turn
            logger.warning("requirement_author_extract_failed", error=str(exc))
            spec = {}
        return spec if isinstance(spec, dict) else {}


def _parse_uuid(value: str) -> UUID:
    return UUID(value)
