"""Role-based context scoping (MET-316).

Maps each agent role to the slice of ``KnowledgeType`` it is allowed to
see during context assembly. Centralises what would otherwise be
copy-pasted into every collector and gives MET-319 / MET-332
(per-agent context specs) a single canonical source.

Design:

* The map is a module-level Python literal — the simplest persistent
  storage that survives a process restart and is trivially diffable in
  PRs. Switching to YAML later is a one-line ``yaml.safe_load`` swap;
  not worth it today.
* ``frozenset`` values so callers can union-merge without mutation.
* ``get_role_knowledge_types(agent_id)`` returns ``None`` for unknown
  roles so callers can distinguish "no role configured" (apply no
  filter) from "role configured with empty allow-list" (filter to
  zero — currently unused but reserved).
* Role identifiers are lowercase, snake_case, and stable. The
  ``ROLE_*`` constants exist so callers don't sprinkle string literals.
"""

from __future__ import annotations

from digital_twin.knowledge.types import KnowledgeType

__all__ = [
    "ROLE_COMPLIANCE_AGENT",
    "ROLE_ELECTRONICS_AGENT",
    "ROLE_MECHANICAL_AGENT",
    "ROLE_SIMULATION_AGENT",
    "all_roles",
    "get_role_knowledge_types",
    "is_known_role",
]


# ---------------------------------------------------------------------------
# Role identifiers
# ---------------------------------------------------------------------------

ROLE_MECHANICAL_AGENT = "mechanical_agent"
ROLE_ELECTRONICS_AGENT = "electronics_agent"
ROLE_SIMULATION_AGENT = "simulation_agent"
ROLE_COMPLIANCE_AGENT = "compliance_agent"


# ---------------------------------------------------------------------------
# Role → allowed knowledge types
# ---------------------------------------------------------------------------
#
# The MET-316 spec sketches per-role knowledge-type lists with sub-tags
# (``design_decision (ME)``, ``component_rationale (ME)``, etc.). The
# current ``KnowledgeType`` enum is intentionally short (5 values); the
# domain split (ME vs EE) is captured via metadata on individual chunks
# (handled in MET-322 conflict detection / MET-326 retrieval metrics).
# Until that split exists as enum values, this map narrows by the
# coarse ``KnowledgeType`` only — the per-domain refinement layers on
# top without breaking this contract.

_ROLE_KNOWLEDGE_TYPES: dict[str, frozenset[KnowledgeType]] = {
    # Mechanical Engineering: structural decisions, component selection,
    # observed failure modes. Constraints come up via the cross-domain
    # constraint engine, not here, so they are deliberately omitted.
    ROLE_MECHANICAL_AGENT: frozenset(
        {
            KnowledgeType.DESIGN_DECISION,
            KnowledgeType.COMPONENT,
            KnowledgeType.FAILURE,
        }
    ),
    # Electronics Engineering: schematic/PCB-level decisions and
    # component datasheets. Failure modes excluded by default — EE
    # failures show up via constraint violations (compliance) and
    # explicit work_product traversals.
    ROLE_ELECTRONICS_AGENT: frozenset(
        {
            KnowledgeType.DESIGN_DECISION,
            KnowledgeType.COMPONENT,
        }
    ),
    # Simulation: prior session summaries (what we tried last time)
    # and failure modes (what previously broke).
    ROLE_SIMULATION_AGENT: frozenset(
        {
            KnowledgeType.SESSION,
            KnowledgeType.FAILURE,
        }
    ),
    # Compliance: regulatory constraints and the design decisions that
    # justify their resolution.
    ROLE_COMPLIANCE_AGENT: frozenset(
        {
            KnowledgeType.CONSTRAINT,
            KnowledgeType.DESIGN_DECISION,
        }
    ),
}


def get_role_knowledge_types(agent_id: str) -> frozenset[KnowledgeType] | None:
    """Return the allowed ``KnowledgeType`` set for a role, or ``None``.

    ``None`` means the agent_id is not a known role — callers should
    leave the search unfiltered (back-compat with the pre-MET-316
    contract).
    """
    return _ROLE_KNOWLEDGE_TYPES.get(agent_id)


def is_known_role(agent_id: str) -> bool:
    return agent_id in _ROLE_KNOWLEDGE_TYPES


def all_roles() -> dict[str, frozenset[KnowledgeType]]:
    """Snapshot copy of the full role map. Safe to mutate."""
    return dict(_ROLE_KNOWLEDGE_TYPES)
