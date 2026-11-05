"""The manufacturing context record and the DFM labels generation uses.

See :mod:`twin_core.manufacturing.context` for the record, its authority and
conflict rule, the prompt block and the truthful labels, and
:mod:`twin_core.manufacturing.proposals` for the candidate processes and
materials proposed when nothing was stated.
"""

from __future__ import annotations

from twin_core.manufacturing.context import (
    CONFIRMED_AUTHORITIES,
    CONTEXT_FIELDS,
    DFM_STATUS_LABELS,
    ILLUSTRATIVE_DFM_STATUSES,
    NOT_ASSESSED_NOTE,
    SPECULATIVE_LABEL,
    UNRESOLVED_REF,
    Authority,
    ContextField,
    ContextUpdate,
    DfmStatus,
    FieldConflict,
    ManufacturingContextRecord,
    MergeResult,
    Provenance,
    apply_updates,
    changed_fields,
    illustrative_dfm_status,
    manufacturing_note,
    normalize_orientation,
    render_prompt_block,
)
from twin_core.manufacturing.proposals import (
    ProcessCandidate,
    parse_envelope_mm,
    propose_candidates,
)

__all__ = [
    "CONFIRMED_AUTHORITIES",
    "CONTEXT_FIELDS",
    "DFM_STATUS_LABELS",
    "ILLUSTRATIVE_DFM_STATUSES",
    "NOT_ASSESSED_NOTE",
    "SPECULATIVE_LABEL",
    "UNRESOLVED_REF",
    "Authority",
    "ContextField",
    "ContextUpdate",
    "DfmStatus",
    "FieldConflict",
    "ManufacturingContextRecord",
    "MergeResult",
    "ProcessCandidate",
    "Provenance",
    "apply_updates",
    "changed_fields",
    "illustrative_dfm_status",
    "manufacturing_note",
    "normalize_orientation",
    "parse_envelope_mm",
    "propose_candidates",
    "render_prompt_block",
]
