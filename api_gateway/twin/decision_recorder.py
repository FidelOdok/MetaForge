"""Design-decision recorder for twin.record_decision (MET-495).

Builds a first-class ``DESIGN_DECISION`` work product instead of a hand-crafted
Cypher node — the constructive fix for the MET-489 class (the prior CR-10
session's raw-Cypher decision nodes were invalid, blob-less, and unlinked,
breaking the twin list in MET-490). One call does all three persistence facets:

1. render the decision to canonical markdown,
2. store the markdown blob in MinIO (graceful — node still created on failure),
3. create a validated ``WorkProduct`` via the twin and link it to its project.

Lives in the api_gateway layer because it composes twin_core (the model), the
``digital_twin.storage`` blob store, and the project backend. It's injected as
an opaque callable into the twin MCP adapter so ``tool_registry`` never imports
any of those layers.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.decision_recorder")


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s or "decision")[:60]


def render_decision_markdown(
    title: str,
    rationale: str,
    alternatives: list[dict[str, Any]] | None,
    supersedes: str | None,
) -> str:
    """Render an ADR-style markdown doc."""
    lines = [f"# {title}", ""]
    if supersedes:
        lines += [f"> Supersedes: `{supersedes}`", ""]
    lines += ["## Decision", "", rationale.strip(), ""]
    if alternatives:
        lines += ["## Alternatives considered", "", "| Option | Why rejected |", "|---|---|"]
        for alt in alternatives:
            if not isinstance(alt, dict):
                continue
            option = str(alt.get("option", "")).replace("|", "\\|")
            reason = str(alt.get("reason_rejected", "")).replace("|", "\\|")
            lines.append(f"| {option} | {reason} |")
        lines.append("")
    return "\n".join(lines)


def make_decision_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` bound to a twin + project backend.

    The returned callable is what the twin MCP adapter invokes; binding the
    dependencies here keeps the adapter free of api_gateway/twin_core imports.
    """

    async def record(
        *,
        title: str,
        rationale: str,
        alternatives: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        supersedes: str | None = None,
        domain: str = "systems",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        with tracer.start_as_current_span("twin.record_decision") as span:
            wp_id = uuid4()
            markdown = render_decision_markdown(title, rationale, alternatives, supersedes)
            content = markdown.encode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()
            filename = f"{_slug(title)}.md"
            span.set_attribute("decision.title", title)

            # 0. Dedup (MET-506): an identical decision (same rendered content)
            #    in the same project is the same decision — return the existing
            #    node instead of creating a duplicate. Skipped when ``supersedes``
            #    is set (a deliberate new record). Best-effort: a query failure
            #    must never block recording.
            if not supersedes:
                try:
                    scope = UUID(project_id) if project_id else None
                    existing = await twin.list_work_products(
                        work_product_type=WorkProductType.DESIGN_DECISION,
                        project_id=scope,
                    )
                    for prior in existing:
                        if getattr(prior, "content_hash", None) == content_hash:
                            prior_id = str(getattr(prior, "id", ""))
                            prior_meta = getattr(prior, "metadata", {}) or {}
                            span.set_attribute("decision.deduplicated", True)
                            logger.info(
                                "decision_deduplicated",
                                node_id=prior_id,
                                project_id=project_id,
                                content_hash=content_hash,
                            )
                            return {
                                "node_id": prior_id,
                                "minio_object_key": prior_meta.get("minio_object_key"),
                                "content_hash": content_hash,
                                "project_linked": bool(project_id),
                                "deduplicated": True,
                            }
                except Exception as exc:  # noqa: BLE001 — dedup never blocks recording
                    logger.warning("decision_dedup_check_failed", error=str(exc))

            # 1. blob → MinIO (graceful: keep the node even if storage is down).
            minio_object_key: str | None = None
            try:
                from digital_twin.storage.work_product_blobs import store_work_product_blob

                minio_object_key = store_work_product_blob(
                    str(wp_id), filename, content, content_type="text/markdown"
                )
            except Exception as exc:  # noqa: BLE001 — degrade like /v1/twin/import
                logger.warning("decision_blob_store_skipped", title=title, error=str(exc))

            metadata: dict[str, Any] = {
                "original_filename": filename,
                "rationale": rationale,
                "alternatives": alternatives or [],
                "content_sha256": content_hash,
                "recorded_by": "twin.record_decision",
            }
            if minio_object_key:
                metadata["minio_object_key"] = minio_object_key
            if supersedes:
                metadata["supersedes"] = supersedes
            if session_id:
                metadata["session_id"] = session_id

            now = datetime.now(UTC)
            wp = WorkProduct(
                id=wp_id,
                name=title,
                type=WorkProductType.DESIGN_DECISION,
                domain=domain,
                file_path="",
                content_hash=content_hash,
                format="md",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by="twin.record_decision",
                project_id=project_id,  # pydantic coerces str → UUID
            )
            created = await twin.create_work_product(wp)
            node_id = str(getattr(created, "id", wp_id))

            # 2. project junction link (MET-489 facet 3) so it shows on the
            #    Projects page, not just the scoped twin view.
            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, node_id, title, "design_decision"
                    )
                    linked = True
                except Exception as exc:  # noqa: BLE001 — link is best-effort
                    logger.warning("decision_project_link_failed", error=str(exc))

            logger.info(
                "decision_recorded",
                node_id=node_id,
                project_id=project_id,
                linked=linked,
                minio_object_key=minio_object_key,
            )
            return {
                "node_id": node_id,
                "minio_object_key": minio_object_key,
                "content_hash": content_hash,
                "project_linked": linked,
                "deduplicated": False,
            }

    return record
