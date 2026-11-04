"""Typed UI blocks an agent reply can carry (generative UI).

A reply is prose plus, optionally, a short list of blocks the dashboard renders
as components: a choice the person answers with a click, a comparison of
options, a request for a measurement, a table, figures against their limits,
a callout, a proposed change to approve, or a part to look at.

Blocks are data, never markup. Each is a closed Pydantic model with bounded
fields, so an agent cannot inject HTML or script, and a malformed block is
refused at the tool call (the agent sees why and can retry) rather than
reaching a browser. ``basis`` keeps presentation truthful: a figure says
whether it is illustrative, estimated, calculated, measured or verified.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

logger = structlog.get_logger(__name__)

MAX_BLOCKS = 8
MAX_BLOCKS_JSON_BYTES = 32_000

Basis = Literal["illustrative", "estimate", "calculated", "measured", "verified"]

_Short = Annotated[str, Field(min_length=1, max_length=120)]
_Line = Annotated[str, Field(min_length=1, max_length=300)]
_Text = Annotated[str, Field(min_length=1, max_length=1000)]
_Cell = Annotated[str, Field(max_length=200)] | float | int | None


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChoiceOption(_Block):
    label: _Short
    # What is sent back when chosen; defaults to the label.
    value: Annotated[str, Field(max_length=500)] | None = None
    description: _Line | None = None


class ChoiceBlock(_Block):
    """A question the person answers by clicking; the answer is sent as their reply."""

    type: Literal["choice"]
    prompt: _Line
    options: Annotated[list[ChoiceOption], Field(min_length=2, max_length=8)]
    multiple: bool = False


class ComparisonOption(_Block):
    name: _Short
    summary: _Line | None = None
    pros: Annotated[list[_Line], Field(max_length=6)] = []
    cons: Annotated[list[_Line], Field(max_length=6)] = []
    # Criterion -> value, for the criteria listed on the block.
    values: Annotated[dict[str, Annotated[str, Field(max_length=120)]], Field(max_length=10)] = {}
    recommended: bool = False


class ComparisonBlock(_Block):
    """Options side by side, with the trade-offs and an optional recommendation."""

    type: Literal["comparison"]
    title: _Line | None = None
    criteria: Annotated[list[_Short], Field(max_length=10)] = []
    options: Annotated[list[ComparisonOption], Field(min_length=2, max_length=5)]
    basis: Basis | None = None


class MeasurementRequestBlock(_Block):
    """Asks the person for a dimension, with its unit and why it matters."""

    type: Literal["measurement_request"]
    label: _Short
    unit: Annotated[str, Field(min_length=1, max_length=16)]
    why: _Line | None = None
    critical: bool = False
    current: float | None = None


class TableBlock(_Block):
    type: Literal["table"]
    title: _Line | None = None
    columns: Annotated[list[_Short], Field(min_length=1, max_length=8)]
    rows: Annotated[list[list[_Cell]], Field(max_length=50)]
    basis: Basis | None = None

    @model_validator(mode="after")
    def _rows_match_columns(self) -> TableBlock:
        for i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(f"row {i} has {len(row)} cells for {len(self.columns)} columns")
        return self


class MetricItem(_Block):
    label: _Short
    value: float
    unit: Annotated[str, Field(max_length=16)] | None = None
    limit: float | None = None
    # Whether the limit is a ceiling ("max") or a floor ("min").
    limit_kind: Literal["max", "min"] = "max"


class MetricsBlock(_Block):
    """Figures, each optionally against its limit."""

    type: Literal["metrics"]
    title: _Line | None = None
    items: Annotated[list[MetricItem], Field(min_length=1, max_length=8)]
    basis: Basis | None = None


class CalloutBlock(_Block):
    type: Literal["callout"]
    tone: Literal["info", "warning", "risk", "success"] = "info"
    title: _Line
    body: _Text | None = None


class ProposalBlock(_Block):
    """A proposed design change, approved or rejected where it is shown."""

    type: Literal["proposal"]
    change_id: _Short
    title: _Line | None = None
    summary: _Text | None = None


class PartBlock(_Block):
    """A work product in the digital twin the reply is about."""

    type: Literal["part"]
    node_id: _Short
    label: _Short | None = None
    note: _Line | None = None


UiBlock = Annotated[
    ChoiceBlock
    | ComparisonBlock
    | MeasurementRequestBlock
    | TableBlock
    | MetricsBlock
    | CalloutBlock
    | ProposalBlock
    | PartBlock,
    Field(discriminator="type"),
]

_BLOCKS = TypeAdapter(list[UiBlock])

BLOCK_TYPES: tuple[str, ...] = (
    "choice",
    "comparison",
    "measurement_request",
    "table",
    "metrics",
    "callout",
    "proposal",
    "part",
)


class InvalidBlocksError(ValueError):
    """The blocks failed validation; the message says which and why."""


def validate_blocks(raw: Any) -> list[dict[str, Any]]:
    """Validate ``raw`` as a list of blocks and return them as plain JSON data.

    Raises :class:`InvalidBlocksError` with a readable reason, which the
    ``chat.render_ui`` tool hands back to the agent.
    """
    if not isinstance(raw, list):
        raise InvalidBlocksError("'blocks' must be a list")
    if len(raw) > MAX_BLOCKS:
        raise InvalidBlocksError(f"at most {MAX_BLOCKS} blocks per reply, got {len(raw)}")
    try:
        blocks = _BLOCKS.validate_python(raw)
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:6]
        )
        raise InvalidBlocksError(reasons) from exc
    data = [b.model_dump(mode="json", exclude_none=True) for b in blocks]
    if len(json.dumps(data)) > MAX_BLOCKS_JSON_BYTES:
        raise InvalidBlocksError(f"blocks exceed {MAX_BLOCKS_JSON_BYTES} bytes; send fewer rows")
    return data


def stored_blocks(raw: Any) -> list[dict[str, Any]]:
    """Blocks read back from storage, dropping any that no longer validate.

    Stored blocks were validated on the way in; this guards a reply written by
    an older schema so one stale block never fails a whole thread read.
    """
    if not raw:
        return []
    kept: list[dict[str, Any]] = []
    for block in raw if isinstance(raw, list) else []:
        try:
            kept.extend(validate_blocks([block]))
        except InvalidBlocksError as exc:
            logger.warning("chat_block_dropped", reason=str(exc))
    return kept
