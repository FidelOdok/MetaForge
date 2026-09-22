"""MCP-tool bridge for the Engineering Change Transaction lifecycle
(FORGE-70, epic FORGE-35).

``twin_core.transactions.ect``'s ``propose_change``/``analyze``/``approve``/
``reject``/``commit``/``mark_rolled_back`` (FORGE-66/67) are real, tested,
and already used internally -- but were never reachable from chat or any
agent, since nothing wired them to an MCP tool. Same injection-seam pattern
every other ``twin.*`` recorder already uses (``make_evidence_recorder``
etc.): ``tool_registry`` can't import ``twin_core`` directly (layering), so
this thin dict<->typed-model glue lives here and gets injected into
``TwinServer`` as one callable bundle, registered all six-or-none since
they're one inseparable state machine.

``approver``/``decided_by`` are agent-asserted identity strings -- exactly
the same trust level every ``created_by`` field already carries throughout
this codebase (``twin.record_decision``, ``twin.record_evidence``, ...),
not a cryptographically verified identity. ``HITLEngine.validate_approver``'s
``IndependenceViolation`` check (an approver can't approve their own patch)
is real and enforced by ``approve()`` below; verifying that the *asserted*
approver name is who they claim to be is a separate, not-yet-built
authentication concern this module doesn't invent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from twin_core.api import TwinAPI
from twin_core.models.engineering_change_transaction import (
    ChangeTrigger,
    EngineeringChangeTransaction,
)
from twin_core.models.patch import Patch
from twin_core.transactions.ect import (
    analyze as _analyze_ect,
)
from twin_core.transactions.ect import (
    approve as _approve_ect,
)
from twin_core.transactions.ect import (
    commit as _commit_ect,
)
from twin_core.transactions.ect import (
    mark_rolled_back as _mark_rolled_back_ect,
)
from twin_core.transactions.ect import (
    propose_change as _propose_change_ect,
)
from twin_core.transactions.ect import (
    reject as _reject_ect,
)


def _serialise(ect: EngineeringChangeTransaction) -> dict[str, Any]:
    return ect.model_dump(mode="json")


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required (non-empty string)")
    return value


def _parse_ect_id(arguments: dict[str, Any]) -> UUID:
    raw = arguments.get("ect_id")
    if not isinstance(raw, str) or not raw:
        raise ValueError("'ect_id' is required (UUID string)")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError(f"'ect_id' must be a valid UUID: {exc}") from exc


def _parse_optional_uuid(arguments: dict[str, Any], key: str) -> UUID | None:
    raw = arguments.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError(f"'{key}' must be a valid UUID: {exc}") from exc


@dataclass
class ECTBridge:
    """Six injected async callables, one per ``twin_core.transactions.ect``
    lifecycle function. Each takes/returns a plain MCP-arguments dict."""

    twin: TwinAPI

    async def propose(self, arguments: dict[str, Any]) -> dict[str, Any]:
        trigger_raw = arguments.get("trigger")
        if not isinstance(trigger_raw, dict):
            raise ValueError("'trigger' is required (object, e.g. {'type': 'user_request'})")
        observation = _require_str(arguments, "observation")
        patch_raw = arguments.get("patch")
        if not isinstance(patch_raw, dict):
            raise ValueError("'patch' is required (object)")
        created_by = arguments.get("created_by", "")
        ect = await _propose_change_ect(
            self.twin,
            trigger=ChangeTrigger.model_validate(trigger_raw),
            observation=observation,
            patch=Patch.model_validate(patch_raw),
            created_by=created_by if isinstance(created_by, str) else "",
            project_id=_parse_optional_uuid(arguments, "project_id"),
        )
        return _serialise(ect)

    async def analyze(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ect = await _analyze_ect(self.twin, _parse_ect_id(arguments))
        return _serialise(ect)

    async def approve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ect_id = _parse_ect_id(arguments)
        approver = _require_str(arguments, "approver")
        ect = await _approve_ect(self.twin, ect_id, approver=approver)
        return _serialise(ect)

    async def reject(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ect_id = _parse_ect_id(arguments)
        reason = _require_str(arguments, "reason")
        decided_by = arguments.get("decided_by", "")
        ect = await _reject_ect(
            self.twin,
            ect_id,
            reason=reason,
            decided_by=decided_by if isinstance(decided_by, str) else "",
        )
        return _serialise(ect)

    async def commit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ect = await _commit_ect(self.twin, _parse_ect_id(arguments))
        return _serialise(ect)

    async def mark_rolled_back(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ect_id = _parse_ect_id(arguments)
        reason = _require_str(arguments, "reason")
        ect = await _mark_rolled_back_ect(self.twin, ect_id, reason=reason)
        return _serialise(ect)


def make_ect_bridge(twin: TwinAPI) -> ECTBridge:
    return ECTBridge(twin=twin)
