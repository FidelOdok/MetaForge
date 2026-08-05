"""Constraint-set recorder — structured requirements into the twin (MET-582).

Builds the async ``record(...)`` callable injected into the twin MCP adapter
as ``constraint_recorder`` (same seam as ``decision_recorder``): one call
persists a batch of structured constraints as evaluable ``Constraint`` nodes
PLUS one ``constraint_set`` work product summarising them, project-linked.

Two design points:

- **Expressions are validated at record time** (``compile(..., "eval")``,
  mirroring the YAML rule loader) so an agent can't park an unevaluable
  string in the twin — a bad expression fails the tool call loudly.
- **Every Constraint node is bound (CONSTRAINED_BY) to the constraint_set
  work product it arrived in.** Constraint-engine violations then cite that
  work product's id, which belongs to the project — so
  ``TwinConstraintChecker`` (MET-583) can scope gate failures to the run's
  project even though the engine itself evaluates branch-wide.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin.document_recorder import make_document_recorder
from observability.tracing import get_tracer
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.constraint_recorder")

_MAX_CONSTRAINTS = 50
_SEVERITIES = {s.value for s in ConstraintSeverity}


def _validate_entry(index: int, entry: Any) -> dict[str, Any]:
    """Validate one constraint spec; returns the normalized fields."""
    if not isinstance(entry, dict):
        raise ValueError(f"constraint[{index}] must be an object")
    name = entry.get("name")
    expression = entry.get("expression")
    if not name or not isinstance(name, str):
        raise ValueError(f"constraint[{index}]: 'name' is required (non-empty string)")
    if not expression or not isinstance(expression, str):
        raise ValueError(f"constraint[{index}] '{name}': 'expression' is required")
    try:
        compile(expression, "<constraint>", "eval")
    except SyntaxError as exc:
        raise ValueError(
            f"constraint[{index}] '{name}': expression does not compile: {exc}"
        ) from exc
    severity = str(entry.get("severity") or "error").lower()
    if severity not in _SEVERITIES:
        raise ValueError(
            f"constraint[{index}] '{name}': severity must be one of {sorted(_SEVERITIES)}"
        )
    return {
        "name": name,
        "expression": expression,
        "severity": severity,
        "message": str(entry.get("message") or ""),
        "domain": str(entry.get("domain") or "systems"),
    }


def _render_markdown(title: str, entries: list[dict[str, Any]]) -> str:
    lines = [f"# Constraint set: {title}", ""]
    for e in entries:
        lines.append(f"## {e['name']} ({e['severity']}, {e['domain']})")
        if e["message"]:
            lines.append(e["message"])
        lines.append(f"```python\n{e['expression']}\n```")
        lines.append("")
    return "\n".join(lines)


def make_constraint_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` that persists a constraint set."""
    record_document = make_document_recorder(twin, project_backend)

    async def record(
        *,
        title: str,
        constraints: list[dict[str, Any]],
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not title or not isinstance(title, str):
            raise ValueError("constraint recorder: 'title' is required (non-empty string)")
        if not isinstance(constraints, list) or not constraints:
            raise ValueError("constraint recorder: 'constraints' must be a non-empty array")
        if len(constraints) > _MAX_CONSTRAINTS:
            raise ValueError(
                f"constraint recorder: at most {_MAX_CONSTRAINTS} constraints per call"
            )
        entries = [_validate_entry(i, c) for i, c in enumerate(constraints)]

        with tracer.start_as_current_span("twin.record_constraint_set") as span:
            span.set_attribute("constraints.count", len(entries))
            # 1) The constraint_set work product first, so each Constraint
            #    node can be bound to it (project attribution for MET-583).
            doc = await record_document(
                content=_render_markdown(title, entries),
                name=title,
                wp_type=WorkProductType.CONSTRAINT_SET,
                domain="systems",
                fmt="md",
                link_type="constraint_set",
                source_tool="twin.record_constraint_set",
                session_id=session_id,
                project_id=project_id,
                extra_metadata={"constraint_count": len(entries)},
            )
            set_wp_id = doc.get("node_id")
            bindings = [UUID(str(set_wp_id))] if set_wp_id else []

            # 2) Evaluable Constraint nodes, each CONSTRAINED_BY the set WP.
            engine = getattr(twin, "constraints", None)
            if engine is None:
                raise ValueError(
                    "constraint recorder: twin exposes no constraint engine "
                    "(.constraints) — cannot record evaluable constraints"
                )
            constraint_ids: list[str] = []
            for e in entries:
                node = Constraint(
                    name=e["name"],
                    expression=e["expression"],
                    severity=ConstraintSeverity(e["severity"]),
                    domain=e["domain"],
                    cross_domain=False,
                    source="twin.record_constraint_set",
                    message=e["message"],
                    metadata={
                        "project_id": project_id,
                        "session_id": session_id,
                        "constraint_set_wp": str(set_wp_id) if set_wp_id else None,
                    },
                )
                created = await engine.add_constraint(node, bindings)
                constraint_ids.append(str(created.id))

            logger.info(
                "constraint_set_recorded",
                title=title,
                constraints=len(constraint_ids),
                project_id=project_id,
                set_wp_id=set_wp_id,
            )
            return {
                "node_id": set_wp_id,
                "constraint_ids": constraint_ids,
                "minio_object_key": doc.get("minio_object_key"),
                "content_hash": doc.get("content_hash"),
                "project_linked": bool(doc.get("project_linked")),
            }

    return record
