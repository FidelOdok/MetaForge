"""twin.delete_work_product: the twin's delete, as a tool that always asks a person.

An agent had a stale M30T model in the twin and no way to remove it from chat.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.node_delete import DeleteRefused, make_work_product_deleter
from mcp_core.annotations import DESTRUCTIVE
from mcp_core.guardrails import never_auto_approved, policy_may_approve
from twin_core.api import OrphanWouldBeCreatedError
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


class _Orphan(OrphanWouldBeCreatedError):
    """The real error, without building an orphan report."""

    def __init__(self) -> None:
        ValueError.__init__(self, "a render depends on it")


def _wp(name: str = "M30T old", **metadata: Any) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="",
        content_hash="x",
        format="step",
        created_by="test",
        metadata=metadata,
    )


class _Twin:
    def __init__(self, wp: WorkProduct, orphan: bool = False) -> None:
        self.wp: WorkProduct | None = wp
        self.orphan = orphan
        self.deleted: list[tuple[UUID, bool]] = []

    async def get_work_product(self, uid: UUID) -> WorkProduct | None:
        return self.wp if self.wp is not None and uid == self.wp.id else None

    async def delete_work_product(self, uid: UUID, cascade: bool = False) -> bool:
        if self.orphan and not cascade:
            raise _Orphan()
        self.deleted.append((uid, cascade))
        self.wp = None
        return True


def _deleter(twin: _Twin, blobs: list[str], links: list[str]) -> Any:
    async def unlink(wp_id: str) -> int:
        links.append(wp_id)
        return 1

    return make_work_product_deleter(twin, unlink_everywhere=unlink, blob_deleter=blobs.append)


async def test_it_deletes_the_node_its_files_and_its_links() -> None:
    wp = _wp(minio_object_key="wp/a.step", mesh_files={"base": "wp/base.glb"})
    twin, blobs, links = _Twin(wp), [], []
    out = await _deleter(twin, blobs, links)(work_product_id=str(wp.id))
    assert out["deleted"] and out["name"] == "M30T old"
    assert twin.deleted == [(wp.id, False)]
    assert blobs == ["wp/a.step", "wp/base.glb"] and links == [str(wp.id)]
    assert out["files_removed"] == 2 and out["links_removed"] == 1


async def test_a_refused_delete_touches_nothing() -> None:
    wp = _wp(minio_object_key="wp/a.step")
    twin, blobs, links = _Twin(wp, orphan=True), [], []
    with pytest.raises(DeleteRefused) as exc:
        await _deleter(twin, blobs, links)(work_product_id=str(wp.id))
    assert exc.value.status == 409 and "nothing was deleted" in exc.value.message
    # The old route removed the file before asking; the file must survive a refusal.
    assert blobs == [] and links == [] and twin.deleted == []


async def test_cascade_deletes_through_dependents() -> None:
    wp = _wp()
    twin, blobs, links = _Twin(wp, orphan=True), [], []
    out = await _deleter(twin, blobs, links)(work_product_id=str(wp.id), cascade=True)
    assert out["cascade"] is True and twin.deleted == [(wp.id, True)]


@pytest.mark.parametrize(("wp_id", "status"), [("nope", 400), (str(UUID(int=7)), 404)])
async def test_a_missing_or_malformed_id_is_refused(wp_id: str, status: int) -> None:
    with pytest.raises(DeleteRefused) as exc:
        await _deleter(_Twin(_wp()), [], [])(work_product_id=wp_id)
    assert exc.value.status == status


def test_no_policy_can_approve_it() -> None:
    assert "twin.delete_work_product" in DESTRUCTIVE
    assert never_auto_approved("twin.delete_work_product")
    assert not policy_may_approve("twin.delete_work_product", "auto")


async def test_the_tool_needs_a_reason_and_passes_it_back() -> None:
    from tool_registry.tools.twin.adapter import TwinServer
    from twin_core.api import InMemoryTwinAPI

    wp = _wp()
    twin = _Twin(wp)
    srv = TwinServer(twin=InMemoryTwinAPI.create(), work_product_deleter=_deleter(twin, [], []))
    assert "twin.delete_work_product" in srv.tool_ids
    with pytest.raises(ValueError, match="reason"):
        await srv.delete_work_product({"work_product_id": str(wp.id)})
    out = await srv.delete_work_product(
        {"work_product_id": str(wp.id), "reason": "superseded by the M30 model"}
    )
    assert out["deleted"] and out["reason"] == "superseded by the M30 model"
    with pytest.raises(ValueError, match="no work product"):
        await srv.delete_work_product({"work_product_id": str(wp.id), "reason": "again"})
