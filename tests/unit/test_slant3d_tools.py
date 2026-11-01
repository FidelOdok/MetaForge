"""Unit tests for the Slant 3D print quoting adapter.

No network: the client is driven through an injected ``httpx.AsyncClient``
backed by ``httpx.MockTransport``. Response bodies follow Slant 3D's published
v2 OpenAPI spec (``{"success": true, "data": ...}`` envelopes, the presigned
upload and confirm flow, ``POST /files/{id}/estimate``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_core.annotations import annotations_for
from tool_registry.tools.slant3d.client import Slant3DClient, parse_file
from tool_registry.tools.slant3d.mcp_adapter import Slant3DMcpServer

PLATFORM_ID = "11111111-1111-4111-8111-111111111111"
FILE_ID = "22222222-2222-4222-8222-222222222222"
FILAMENT_ID = "33333333-3333-4333-8333-333333333333"
STL_BYTES = b"solid bracket\nendsolid bracket\n"
UPLOAD_URL = "https://slant-files.s3.amazonaws.com/upload/abc?X-Amz-Signature=sig"

METRICS = {
    "x": 40.0,
    "y": 20.0,
    "z": 10.0,
    "volume": 6.2,
    "surfaceArea": 3100.0,
    "weight": 7.7,
    "printTimeEstimate": 48,
    "supportRequired": False,
    "bodyCount": 1,
    "imageURL": "https://example.com/preview.png",
    "units": {"x": "mm"},
}


def ok(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"success": True, "message": "ok", "data": data})


class FakeSlant:
    """A scripted Slant 3D: records requests, replies like the documented API."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.platforms: list[dict[str, Any]] = [{"id": PLATFORM_ID, "name": "MetaForge"}]
        self.upload_status = 200
        self.error: tuple[int, dict[str, Any]] | None = None
        self.metrics: dict[str, Any] = METRICS

    def file(self) -> dict[str, Any]:
        return {
            "publicFileServiceId": FILE_ID,
            "name": "bracket.stl",
            "platformId": PLATFORM_ID,
            "type": "stl",
            "notes": None,
            "metrics": self.metrics,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "slant-files.s3.amazonaws.com":
            return httpx.Response(self.upload_status)
        if self.error is not None:
            status, body = self.error
            return httpx.Response(status, json=body)
        path = request.url.path.removeprefix("/v2/api")
        if path == "/platforms":
            return ok(self.platforms)
        if path == "/files/direct-upload":
            placeholder = {k: v for k, v in self.file().items() if k != "metrics"}
            return ok({"presignedUrl": UPLOAD_URL, "key": "k", "filePlaceholder": placeholder})
        if path == "/files/confirm-upload":
            return ok(self.file())
        if path == f"/files/{FILE_ID}/estimate":
            return ok(
                {
                    "subtotal": 9.0,
                    "total": 10.5,
                    "pricePerUnit": 3.0,
                    "quantity": 3,
                    "totalMaterial": 23.1,
                    "estimatedPrintTime": 48,
                    "bodyChargeCost": 0,
                    "oversizeSurcharge": 1.5,
                }
            )
        if path == f"/files/{FILE_ID}":
            return ok({**self.file(), "notes": "Printed upright"})
        if path == "/filaments":
            return ok(
                [
                    {
                        "publicId": FILAMENT_ID,
                        "name": "PETG Black",
                        "provider": "Polymaker",
                        "profile": "PETG",
                        "color": "black",
                        "hexValue": "#000000",
                        "available": True,
                    }
                ]
            )
        return httpx.Response(404, json={"success": False, "message": "Not found"})


def make_server(
    fake: FakeSlant, workspace: Path, key: str = "sl-test", platform_id: str | None = None
) -> Slant3DMcpServer:
    client = Slant3DClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        api_key=key,
        base_url="https://slant3dapi.com/v2/api",
        platform_id=platform_id,
    )
    return Slant3DMcpServer(client=client, workspace_dir=workspace)


def put(workspace: Path, name: str = "bracket.stl", data: bytes = STL_BYTES) -> str:
    path = workspace / "output" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"output/{name}"


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------


async def test_analyze_uploads_through_the_presigned_flow(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)

    result = await server.handle_analyze_part({"file_path": put(tmp_path)})

    platforms, direct, upload, confirm = fake.requests
    assert platforms.url.path == "/v2/api/platforms"
    assert direct.headers["authorization"] == "Bearer sl-test"
    assert body(direct) == {"name": "bracket.stl", "platformId": PLATFORM_ID}
    assert str(upload.url) == UPLOAD_URL
    assert upload.method == "PUT"
    assert upload.content == STL_BYTES
    # The storage host gets the signed URL, never the API key.
    assert "authorization" not in upload.headers
    assert body(confirm)["filePlaceholder"]["publicFileServiceId"] == FILE_ID

    assert result["file_id"] == FILE_ID
    assert result["metrics"] == {
        "size_x_mm": 40.0,
        "size_y_mm": 20.0,
        "size_z_mm": 10.0,
        "volume_cm3": 6.2,
        "surface_area_mm2": 3100.0,
        "weight_g": 7.7,
        "print_time_minutes": 48,
        "support_required": False,
        "body_count": 1,
    }


async def test_a_configured_platform_skips_the_lookup(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path, platform_id="custom-platform")
    await server.handle_analyze_part({"file_path": put(tmp_path)})
    assert fake.requests[0].url.path == "/v2/api/files/direct-upload"
    assert body(fake.requests[0])["platformId"] == "custom-platform"


async def test_an_account_with_no_platform_says_what_to_do(tmp_path: Path) -> None:
    fake = FakeSlant()
    fake.platforms = []
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="SLANT3D_PLATFORM_ID"):
        await server.handle_analyze_part({"file_path": put(tmp_path)})


@pytest.mark.parametrize(
    ("file_path", "message"),
    [
        ("../outside.stl", "outside the adapter workspace"),
        ("output/part.step", "not an STL"),
        ("output/missing.stl", "no file"),
    ],
)
async def test_analyze_refuses_bad_files(tmp_path: Path, file_path: str, message: str) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path / "ws")
    with pytest.raises(ValueError, match=message):
        await server.handle_analyze_part({"file_path": file_path})
    assert fake.requests == []


async def test_an_empty_stl_is_refused(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="empty"):
        await server.handle_analyze_part({"file_path": put(tmp_path, data=b"")})
    assert fake.requests == []


async def test_a_failed_storage_upload_is_an_error(tmp_path: Path) -> None:
    fake = FakeSlant()
    fake.upload_status = 403
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="HTTP 403"):
        await server.handle_analyze_part({"file_path": put(tmp_path)})
    assert not any(r.url.path.endswith("confirm-upload") for r in fake.requests)


def test_older_records_fall_back_to_stl_metrics() -> None:
    file = parse_file(
        {"publicFileServiceId": FILE_ID, "name": "a.stl", "STLMetrics": {"x": 5, "weight": 2.0}}
    )
    assert file.metrics == {"size_x_mm": 5, "weight_g": 2.0}


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


async def test_quote_sends_filament_and_quantity_and_names_units(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)

    result = await server.handle_quote(
        {"file_id": FILE_ID, "filament_id": FILAMENT_ID, "quantity": 3}
    )

    request = fake.requests[0]
    assert request.url.path == f"/v2/api/files/{FILE_ID}/estimate"
    assert body(request) == {"options": {"quantity": 3, "filamentId": FILAMENT_ID}}
    assert result["currency"] == "USD"
    assert result["total_usd"] == 10.5
    assert result["price_per_unit_usd"] == 3.0
    assert result["oversize_surcharge_usd"] == 1.5
    assert result["quantity"] == 3
    assert "shipping" in result["note"]


async def test_quote_defaults_to_one_unit_of_the_default_filament(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)
    result = await server.handle_quote({"file_id": FILE_ID})
    assert body(fake.requests[0]) == {"options": {"quantity": 1}}
    assert result["filament_id"] == "default (PLA black)"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"file_id": "../x"}, "not a Slant 3D file id"),
        ({"file_id": FILE_ID, "filament_id": "black"}, "not a Slant 3D filament id"),
        ({"file_id": FILE_ID, "quantity": 0}, "between 1 and"),
        ({"file_id": FILE_ID, "quantity": True}, "must be an integer"),
    ],
)
async def test_quote_refuses_bad_arguments(
    tmp_path: Path, arguments: dict[str, Any], message: str
) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match=message):
        await server.handle_quote(arguments)
    assert fake.requests == []


async def test_a_file_from_another_account_is_named(tmp_path: Path) -> None:
    fake = FakeSlant()
    fake.error = (
        401,
        {"success": False, "message": "File belongs to another creator", "error": "forbidden"},
    )
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="another account.*File belongs to another creator"):
        await server.handle_quote({"file_id": FILE_ID})


# ---------------------------------------------------------------------------
# Reads, errors, configuration
# ---------------------------------------------------------------------------


async def test_get_part_returns_metrics_and_notes(tmp_path: Path) -> None:
    server = make_server(FakeSlant(), tmp_path)
    result = await server.handle_get_part({"file_id": FILE_ID})
    assert result["notes"] == "Printed upright"
    assert result["metrics"]["weight_g"] == 7.7


async def test_list_filaments_filters_and_maps(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)

    result = await server.handle_list_filaments({"materials": ["petg"], "colors": ["black"]})

    params = fake.requests[0].url.params
    assert params["profile"] == "PETG"
    assert params["color"] == "black"
    assert result == {
        "filaments": [
            {
                "filament_id": FILAMENT_ID,
                "name": "PETG Black",
                "material": "PETG",
                "color": "black",
                "hex": "#000000",
                "available": True,
            }
        ],
        "count": 1,
    }


async def test_an_unknown_material_is_refused_before_the_call(tmp_path: Path) -> None:
    fake = FakeSlant()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="unknown material"):
        await server.handle_list_filaments({"materials": ["ABS"]})
    assert fake.requests == []


async def test_the_filament_error_shape_is_read(tmp_path: Path) -> None:
    fake = FakeSlant()
    fake.error = (400, {"success": False, "error": {"message": "bad profile", "statusCode": 400}})
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="bad profile"):
        await server.handle_list_filaments({})


async def test_unreachable_api_is_an_error(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = Slant3DClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(boom)), api_key="k"
    )
    server = Slant3DMcpServer(client=client, workspace_dir=tmp_path)
    with pytest.raises(ValueError, match="could not reach Slant 3D"):
        await server.handle_list_filaments({})


async def test_missing_key_is_refused_without_a_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SLANT3D_API_KEY", raising=False)
    fake = FakeSlant()
    server = make_server(fake, tmp_path, key="")
    with pytest.raises(ValueError, match="SLANT3D_API_KEY is unset"):
        await server.handle_list_filaments({})
    assert fake.requests == []


def test_configuration_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SLANT3D_BASE_URL", "https://proxy.example/v2/api/")
    monkeypatch.setenv("SLANT3D_PLATFORM_ID", PLATFORM_ID)
    client = Slant3DClient(api_key="k")
    assert client._base_url == "https://proxy.example/v2/api"
    assert client._platform_id == PLATFORM_ID


# ---------------------------------------------------------------------------
# Registration and policy
# ---------------------------------------------------------------------------


def test_registers_four_tools_and_no_order_tool(tmp_path: Path) -> None:
    server = make_server(FakeSlant(), tmp_path)
    assert sorted(server.tool_ids) == [
        "slant3d.analyze_part",
        "slant3d.get_part",
        "slant3d.list_filaments",
        "slant3d.quote",
    ]


def test_annotations() -> None:
    upload = annotations_for("slant3d.analyze_part")
    assert upload["readOnlyHint"] is False
    assert upload["destructiveHint"] is False
    assert upload["openWorldHint"] is True
    for tool_id in ("slant3d.quote", "slant3d.get_part", "slant3d.list_filaments"):
        hints = annotations_for(tool_id)
        assert hints["readOnlyHint"] is True
        assert hints["openWorldHint"] is True


async def test_bootstrap_registers_slant3d_only_with_a_key(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.delenv("SLANT3D_API_KEY", raising=False)
    registry = await bootstrap_tool_registry(adapter_ids=["slant3d"])
    assert registry.list_adapters() == []

    monkeypatch.setenv("SLANT3D_API_KEY", "sl-test")
    registry = await bootstrap_tool_registry(adapter_ids=["slant3d"])
    assert [a.adapter_id for a in registry.list_adapters()] == ["slant3d"]


async def test_bootstrap_honours_the_per_adapter_toggle(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.setenv("SLANT3D_API_KEY", "sl-test")
    monkeypatch.setenv("METAFORGE_ADAPTER_SLANT3D_ENABLED", "false")
    registry = await bootstrap_tool_registry(adapter_ids=["slant3d"])
    assert registry.list_adapters() == []
