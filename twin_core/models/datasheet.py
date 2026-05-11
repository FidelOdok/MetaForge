"""Datasheet node — a versioned manufacturer datasheet (MET-430).

A Datasheet binds a specific PDF revision to an MPN. Distinct from a
generic ``WorkProduct`` because:

* Manufacturer + MPN are first-class fields (so ``knowledge.extract``
  can scope by MPN — see MET-422).
* Revisions chain via ``SUPERSEDES`` edges: when a newer revision is
  ingested, it points at the prior one. The "current" datasheet for
  an MPN is the one with no other datasheet superseding it.
* ``file_hash`` provides idempotent ingest — re-ingesting the same
  bytes is a no-op.

This module ships the **model** only. The PDF chunking / table
extraction / structured-property pipeline lands as a follow-up under
the same ticket.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class Datasheet(NodeBase):
    """A versioned manufacturer datasheet.

    Identified by ``(mpn, manufacturer, revision)``. The ``file_hash``
    is the SHA-256 of the source PDF — re-ingesting the same bytes
    must yield the same node id.
    """

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.DATASHEET

    mpn: str = Field(..., description="Manufacturer part number this datasheet covers.")
    manufacturer: str = Field(..., description='Manufacturer name (e.g. "STMicroelectronics").')
    revision: str = Field(..., description='Datasheet revision string (e.g. "rev9", "v2.4").')
    file_hash: str = Field(
        ...,
        description=(
            "SHA-256 of the source PDF. Used for idempotent ingest — "
            "same hash means same node id."
        ),
    )
    source_path: str = Field(
        default="",
        description="Local path or URL of the source PDF.",
    )
    source_url: str | None = Field(
        default=None,
        description="Canonical URL when fetched from a manufacturer site.",
    )
    page_count: int = Field(
        default=0,
        description="Total number of pages in the PDF.",
    )
    published_at: datetime | None = Field(
        default=None,
        description="Publication date pulled off the cover page.",
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this revision was ingested by MetaForge.",
    )
    metadata: dict = Field(
        default_factory=dict,
        description=(
            "Free-form extracted metadata (cover-page fields, table of "
            "contents anchors). Schema-less by design — the typed "
            "property extraction pipeline (MET-422) reads from here."
        ),
    )
