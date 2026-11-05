"""Reading interface contracts and their agreements from the twin.

A contract is a definition item (``IFC-<kind>``) whose head revision holds the
contract under ``metadata.interface_contract``; an agreement is a design
decision whose ``metadata.contract_agreement`` names the contract key, the
revision and the owning domain it agrees for. Both are read here so the
gateway and the derived interface edges read them the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from twin_core.models.enums import WorkProductType
from twin_core.models.interface_contract import (
    AGREEMENT_META_KEY,
    CONTRACT_ITEM_TYPE,
    CONTRACT_META_KEY,
    INTERFACE_VERSION_KEY,
    ContractAgreement,
    InterfaceContract,
)

logger = structlog.get_logger(__name__)


@dataclass
class StoredContract:
    """One contract at its head revision."""

    key: str
    revision: int
    node_id: str
    contract: InterfaceContract
    metadata: dict[str, Any]

    @property
    def interface_version(self) -> int:
        return int(self.metadata.get(INTERFACE_VERSION_KEY) or 1)

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.revision}"


def _pid(project_id: UUID | str) -> UUID:
    return project_id if isinstance(project_id, UUID) else UUID(str(project_id))


def contract_from_metadata(meta: dict[str, Any] | None) -> InterfaceContract | None:
    raw = (meta or {}).get(CONTRACT_META_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return InterfaceContract.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - one unreadable revision must not hide the rest
        logger.warning("interface_contract_unreadable", error=str(exc))
        return None


async def list_contracts(twin: Any, project_id: UUID | str) -> list[StoredContract]:
    """Every contract in the project at its head, by key."""
    from twin_core.items import list_items

    pid = _pid(project_id)
    out: list[StoredContract] = []
    for item in await list_items(twin, project_id=pid, item_type=CONTRACT_ITEM_TYPE):
        if item.head_node_id is None:
            continue
        node = await twin.graph.get_node(item.head_node_id)
        meta = dict(getattr(node, "metadata", None) or {})
        contract = contract_from_metadata(meta)
        if contract is None:
            continue
        out.append(
            StoredContract(
                key=item.key,
                revision=item.head_revision,
                node_id=str(item.head_node_id),
                contract=contract,
                metadata=meta,
            )
        )
    return sorted(out, key=lambda c: c.key)


def agreement_from_metadata(
    node_id: str, meta: dict[str, Any] | None
) -> tuple[str, ContractAgreement] | None:
    """``(contract key, agreement)`` from a decision's metadata, or ``None``."""
    raw = (meta or {}).get(AGREEMENT_META_KEY)
    if not isinstance(raw, dict) or not raw.get("contract_key"):
        return None
    try:
        agreement = ContractAgreement(
            domain=str(raw.get("domain") or ""),
            contract_revision=int(raw.get("contract_revision") or 0),
            interface_version=int(
                raw.get("interface_version") or raw.get("contract_revision") or 0
            ),
            decided_by=str(raw.get("decided_by") or ""),
            decision_id=node_id,
            at=str(raw.get("at") or ""),
            rationale=str(raw.get("rationale") or ""),
        )
    except Exception:  # noqa: BLE001 - a malformed agreement agrees to nothing
        return None
    if not agreement.domain:
        return None
    return str(raw["contract_key"]), agreement


async def read_agreements(twin: Any, project_id: UUID | str) -> dict[str, list[ContractAgreement]]:
    """Every recorded agreement in the project, by contract key, oldest first."""
    pid = _pid(project_id)
    decisions = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_DECISION, project_id=pid
    )
    out: dict[str, list[ContractAgreement]] = {}
    for node in decisions:
        found = agreement_from_metadata(str(node.id), getattr(node, "metadata", None))
        if found is not None:
            out.setdefault(found[0], []).append(found[1])
    for agreements in out.values():
        agreements.sort(key=lambda a: a.at)
    return out
