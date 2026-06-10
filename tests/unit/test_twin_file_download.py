"""Tests for work-product file download / open / preview (MET-483).

Covers the gateway route ``GET /v1/twin/nodes/{id}/file`` (local-path and
MinIO-object-key resolution, disposition, content-type, error paths) and
the ``blob_store`` key/put/get helpers with an injected fake MinIO client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_gateway.twin import blob_store, routes
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


def _wp(*, file_path: str = "", metadata: dict | None = None, fmt: str = "csv") -> WorkProduct:
    now = datetime.now(UTC)
    return WorkProduct(
        id=uuid4(),
        name="drone-bom",
        type=WorkProductType.BOM,
        domain="electronics",
        file_path=file_path,
        content_hash="",
        format=fmt,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
        created_by="test",
    )


async def _seed(wp: WorkProduct) -> str:
    twin = InMemoryTwinAPI.create()
    await twin.create_work_product(wp)
    routes._twin = twin  # route reads the module-level twin
    return str(wp.id)


# ---------------------------------------------------------------------------
# Route: local file_path
# ---------------------------------------------------------------------------


async def test_download_inline_serves_local_file(tmp_path) -> None:
    f = tmp_path / "bom.csv"
    f.write_text("ref,mpn\nU1,STM32F405\n", encoding="utf-8")
    node_id = await _seed(_wp(file_path=str(f)))

    resp = await routes.download_node_file(node_id, download=False)

    assert resp.status_code == 200
    assert resp.media_type == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"].startswith("inline")
    assert b"STM32F405" in resp.body


async def test_download_attachment_disposition(tmp_path) -> None:
    f = tmp_path / "bom.csv"
    f.write_bytes(b"x,y\n1,2\n")
    node_id = await _seed(_wp(file_path=str(f)))

    resp = await routes.download_node_file(node_id, download=True)

    assert resp.headers["content-disposition"].startswith("attachment")


async def test_missing_file_path_returns_404() -> None:
    node_id = await _seed(_wp(file_path=""))
    with pytest.raises(HTTPException) as exc:
        await routes.download_node_file(node_id, download=False)
    assert exc.value.status_code == 404
    assert "no stored file" in str(exc.value.detail).lower()


async def test_file_path_not_on_disk_returns_404(tmp_path) -> None:
    node_id = await _seed(_wp(file_path=str(tmp_path / "gone.csv")))
    with pytest.raises(HTTPException) as exc:
        await routes.download_node_file(node_id, download=False)
    assert exc.value.status_code == 404


async def test_bad_node_id_returns_400() -> None:
    await _seed(_wp(file_path=""))
    with pytest.raises(HTTPException) as exc:
        await routes.download_node_file("not-a-uuid", download=False)
    assert exc.value.status_code == 400


async def test_unknown_node_returns_404() -> None:
    await _seed(_wp(file_path=""))
    with pytest.raises(HTTPException) as exc:
        await routes.download_node_file(str(uuid4()), download=False)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Route: MinIO object-key path
# ---------------------------------------------------------------------------


async def test_download_from_minio_object_key(monkeypatch) -> None:
    node_id = await _seed(
        _wp(metadata={"minio_object_key": "work-products/x/datasheet.pdf"}, fmt="pdf")
    )
    monkeypatch.setattr(blob_store, "fetch_work_product_blob", lambda key: b"%PDF-1.7 fake")

    resp = await routes.download_node_file(node_id, download=False)

    assert resp.status_code == 200
    assert resp.media_type == "application/pdf"
    assert resp.body == b"%PDF-1.7 fake"


# ---------------------------------------------------------------------------
# Content-type mapping
# ---------------------------------------------------------------------------


def test_content_type_from_format() -> None:
    assert routes._content_type_for("pdf", "x") == "application/pdf"
    assert routes._content_type_for("png", "x") == "image/png"
    assert routes._content_type_for("kicad_sch", "x").startswith("text/plain")


def test_content_type_falls_back_to_octet_stream() -> None:
    assert routes._content_type_for("", "blob.bin") == "application/octet-stream"
    assert routes._content_type_for("xyz", "f.unknownext") == "application/octet-stream"


def test_content_type_uses_filename_suffix_when_format_blank() -> None:
    assert routes._content_type_for("", "report.pdf") == "application/pdf"


# ---------------------------------------------------------------------------
# blob_store
# ---------------------------------------------------------------------------


def test_object_key_layout() -> None:
    key = blob_store.work_product_object_key("abc-123", "main.kicad_sch")
    assert key == "work-products/abc-123/main.kicad_sch"


def test_object_key_sanitizes_slashes() -> None:
    key = blob_store.work_product_object_key("n1", "a/b/c.csv")
    assert key == "work-products/n1/a_b_c.csv"


class _FakeMinio:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}
        self.buckets: set[str] = set()

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(self, bucket, key, data, length, content_type=None):  # noqa: ANN001
        self.store[(bucket, key)] = data.read()

    def get_object(self, bucket, key):  # noqa: ANN001
        payload = self.store[(bucket, key)]

        class _Resp:
            def read(self_inner) -> bytes:
                return payload

            def close(self_inner) -> None:
                pass

            def release_conn(self_inner) -> None:
                pass

        return _Resp()


def test_blob_store_put_get_roundtrip() -> None:
    from digital_twin.storage.minio_adapter import MinIOSettings

    settings = MinIOSettings(
        endpoint="x:9000", access_key="a", secret_key="b", bucket="metaforge-kb", secure=False
    )
    fake = _FakeMinio()
    key = blob_store.store_work_product_blob(
        "node-1", "bom.csv", b"ref,mpn\n", content_type="text/csv", client=fake, settings=settings
    )
    assert key == "work-products/node-1/bom.csv"
    assert "metaforge-kb" in fake.buckets  # bucket auto-created
    got = blob_store.fetch_work_product_blob(key, client=fake, settings=settings)
    assert got == b"ref,mpn\n"


# ---------------------------------------------------------------------------
# DELETE /v1/twin/nodes/{id} (MET-484)
# ---------------------------------------------------------------------------


async def test_delete_node_removes_it() -> None:
    node_id = await _seed(_wp(file_path=""))
    await routes.delete_node(node_id, cascade=False)
    with pytest.raises(HTTPException) as exc:
        await routes.get_twin_node(node_id)
    assert exc.value.status_code == 404


async def test_delete_unknown_node_404() -> None:
    await _seed(_wp(file_path=""))
    with pytest.raises(HTTPException) as exc:
        await routes.delete_node(str(uuid4()), cascade=False)
    assert exc.value.status_code == 404


async def test_delete_bad_id_400() -> None:
    await _seed(_wp(file_path=""))
    with pytest.raises(HTTPException) as exc:
        await routes.delete_node("not-a-uuid", cascade=False)
    assert exc.value.status_code == 400


async def test_delete_calls_blob_delete(monkeypatch) -> None:
    node_id = await _seed(_wp(metadata={"minio_object_key": "work-products/x/y.pdf"}, fmt="pdf"))
    called = {}
    monkeypatch.setattr(
        blob_store, "delete_work_product_blob", lambda key: called.setdefault("key", key)
    )
    await routes.delete_node(node_id, cascade=False)
    assert called.get("key") == "work-products/x/y.pdf"


async def test_delete_removes_project_link() -> None:
    from api_gateway.projects import routes as proutes

    node_id = await _seed(_wp(file_path=""))
    project = await proutes._backend.create_project(name="P", description="", status="draft")
    await proutes._backend.link_work_product(project.id, node_id, "WP", "bom")
    await routes.delete_node(node_id, cascade=False)
    refreshed = await proutes._backend.get_project(project.id)
    assert all(w.id != node_id for w in refreshed.work_products)
