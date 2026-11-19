"""Delete a work product: the node, its stored files and its project links (MET-484).

Shared by ``DELETE /v1/twin/nodes/{id}`` and the ``twin.delete_work_product``
MCP tool, so both refuse and clean up the same way.

The order matters. The node goes first: a delete that would orphan dependents
is refused there, and nothing has been touched yet. Only then are the stored
files removed (best effort: a storage failure does not undo a delete that has
happened) and the project links dropped. The route used to remove the file
before asking, so a refused delete still lost the file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.node_delete")


class DeleteRefused(Exception):
    """A delete that was not done, with the HTTP status that says why."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _blob_keys(metadata: dict[str, Any]) -> list[str]:
    """The work product's stored objects: its primary blob and any per-link meshes."""
    keys: list[str] = []
    primary = metadata.get("minio_object_key")
    if isinstance(primary, str) and primary:
        keys.append(primary)
    meshes = metadata.get("mesh_files")
    if isinstance(meshes, dict):
        keys += [k for k in meshes.values() if isinstance(k, str) and k]
    return keys


def make_work_product_deleter(
    twin: Any,
    *,
    unlink_everywhere: Callable[[str], Awaitable[int]],
    blob_deleter: Callable[[str], Any] | None = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return an async ``delete(*, work_product_id, cascade=False) -> dict``.

    ``unlink_everywhere(wp_id) -> count`` drops the work product from every
    project; ``blob_deleter(key)`` removes one stored object (MinIO by default).
    """

    def _default_blob_deleter(key: str) -> None:
        from api_gateway.twin.blob_store import delete_work_product_blob

        delete_work_product_blob(key)

    remove_blob = blob_deleter or _default_blob_deleter

    async def delete(*, work_product_id: str, cascade: bool = False) -> dict[str, Any]:
        from twin_core.api import OrphanWouldBeCreatedError

        with tracer.start_as_current_span("twin.delete_work_product") as span:
            span.set_attribute("twin.node_id", str(work_product_id))
            span.set_attribute("twin.cascade", cascade)
            try:
                uid = UUID(str(work_product_id))
            except ValueError as exc:
                raise DeleteRefused(f"invalid work_product_id {work_product_id!r}", 400) from exc
            wp = await twin.get_work_product(uid)
            if wp is None:
                raise DeleteRefused(f"no work product {work_product_id!r}", 404)
            keys = _blob_keys(dict(wp.metadata or {}))
            try:
                await twin.delete_work_product(uid, cascade=cascade)
            except OrphanWouldBeCreatedError as exc:
                span.record_exception(exc)
                raise DeleteRefused(
                    f"deleting {wp.name!r} would leave other work products without the node "
                    f"they depend on ({exc}); nothing was deleted. Delete those first, or "
                    "ask again with cascade to delete them too.",
                    409,
                ) from exc
            removed = 0
            for key in keys:
                try:
                    remove_blob(key)
                    removed += 1
                except Exception as exc:  # noqa: BLE001 - best effort after the delete
                    logger.warning("wp_blob_delete_failed", key=key, error=str(exc))
            links_removed = await unlink_everywhere(str(uid))
            logger.info(
                "twin_node_deleted",
                node_id=str(uid),
                name=wp.name,
                cascade=cascade,
                links_removed=links_removed,
                blobs_removed=removed,
            )
            return {
                "deleted": True,
                "work_product_id": str(uid),
                "name": wp.name,
                "cascade": cascade,
                "links_removed": links_removed,
                "files_removed": removed,
            }

    return delete
