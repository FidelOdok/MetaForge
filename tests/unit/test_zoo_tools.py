"""Unit tests for the Zoo cloud CAD conversion adapter.

No network: the client is driven through an injected ``httpx.AsyncClient``
backed by ``httpx.MockTransport``. Response bodies follow the shapes in Zoo's
published OpenAPI spec (``FileConversion``, ``FileVolume``, ...,
``AsyncApiCallOutput``).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_core.annotations import annotations_for
from tool_registry.tools.zoo.client import (
    ZooClient,
    ZooError,
    detect_format,
    parse_operation,
    safe_output_name,
)
from tool_registry.tools.zoo.mcp_adapter import ZooMcpServer

OP_ID = "6f1b8f0e-2c7a-4b1e-9a77-0d5f4b9b2e11"
STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
PROPERTY_VALUES: dict[str, Any] = {
    "volume": {"volume": 12000.0},
    "surface-area": {"surface_area": 3400.0},
    "center-of-mass": {"center_of_mass": {"x": 10.0, "y": 5.0, "z": 2.5}},
    "bounding-box": {
        "bounding_box": {
            "center": {"x": 10.0, "y": 5.0, "z": 2.5},
            "dimensions": {"x": 20.0, "y": 10.0, "z": 5.0},
        }
    },
    "mass": {"mass": 32.4},
}


class FakeZoo:
    """A scripted Zoo: records requests, replies like the documented API."""

    def __init__(self, conversion_statuses: list[str] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.conversion_statuses = conversion_statuses or ["completed"]
        self.outputs: dict[str, str] = {"output.step": base64.b64encode(STEP_BYTES).decode()}
        self.error_status: int | None = None

    def _conversion(self, status: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": OP_ID,
            "type": "file_conversion",
            "status": status,
            "src_format": "sldprt",
            "output_format": "step",
        }
        if status == "completed":
            body["outputs"] = self.outputs
        if status == "failed":
            body["error"] = "the part has no solid bodies"
        return body

    def _next_status(self) -> str:
        if len(self.conversion_statuses) > 1:
            return self.conversion_statuses.pop(0)
        return self.conversion_statuses[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error_status is not None:
            return httpx.Response(
                self.error_status, json={"message": "nope", "request_id": "req-1"}
            )
        path = request.url.path
        if path.startswith("/file/conversion/"):
            return httpx.Response(201, json=self._conversion(self._next_status()))
        if path.startswith("/async/operations/"):
            return httpx.Response(200, json=self._conversion(self._next_status()))
        endpoint = path.removeprefix("/file/")
        if endpoint in PROPERTY_VALUES:
            body = {"id": OP_ID, "status": "completed", **PROPERTY_VALUES[endpoint]}
            return httpx.Response(201, json=body)
        return httpx.Response(404, json={"message": "no route", "request_id": "req-2"})


def make_server(fake: FakeZoo, workspace: Path, token: str = "zoo-token") -> ZooMcpServer:
    client = ZooClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        api_token=token,
    )
    server = ZooMcpServer(client=client, workspace_dir=workspace)
    server.poll_interval_seconds = 0
    return server


def put(workspace: Path, name: str, data: bytes = b"native cad bytes") -> str:
    path = workspace / "input" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"input/{name}"


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


async def test_convert_uploads_the_file_and_writes_the_step(tmp_path: Path) -> None:
    fake = FakeZoo()
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "bracket.SLDPRT")

    result = await server.handle_convert({"file_path": rel, "output_format": "step"})

    request = fake.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/file/conversion/sldprt/step"
    assert request.headers["authorization"] == "Bearer zoo-token"
    assert request.headers["content-type"] == "application/octet-stream"
    assert request.content == b"native cad bytes"

    assert result["status"] == "completed"
    assert result["file_path"] == f"output/zoo/{OP_ID}/output.step"
    assert (tmp_path / result["file_path"]).read_bytes() == STEP_BYTES
    assert result["files"] == [
        {"format": "step", "file_path": result["file_path"], "size_bytes": len(STEP_BYTES)}
    ]
    assert "note" not in result


async def test_a_large_file_is_polled_until_the_conversion_completes(tmp_path: Path) -> None:
    fake = FakeZoo(conversion_statuses=["queued", "in_progress", "completed"])
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "assembly.step")

    result = await server.handle_convert({"file_path": rel, "output_format": "step"})

    assert result["status"] == "completed"
    assert [r.url.path for r in fake.requests[1:]] == [f"/async/operations/{OP_ID}"] * 2


async def test_without_waiting_a_queued_conversion_returns_its_operation_id(
    tmp_path: Path,
) -> None:
    fake = FakeZoo(conversion_statuses=["queued", "completed"])
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "assembly.step")

    queued = await server.handle_convert(
        {"file_path": rel, "output_format": "step", "wait_seconds": 0}
    )
    assert queued["status"] == "queued"
    assert queued["operation_id"] == OP_ID
    assert queued["files"] == []
    assert "zoo.get_conversion" in queued["note"]

    done = await server.handle_get_conversion({"operation_id": OP_ID})
    assert done["status"] == "completed"
    assert (tmp_path / done["file_path"]).read_bytes() == STEP_BYTES


async def test_a_failed_conversion_reports_zoos_reason(tmp_path: Path) -> None:
    fake = FakeZoo(conversion_statuses=["failed"])
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "bracket.sldprt")

    with pytest.raises(ValueError, match="no solid bodies"):
        await server.handle_convert({"file_path": rel, "output_format": "step"})


async def test_a_mesh_to_step_conversion_says_the_result_is_faceted(tmp_path: Path) -> None:
    server = make_server(FakeZoo(), tmp_path)
    rel = put(tmp_path, "scan.stl")

    result = await server.handle_convert({"file_path": rel, "output_format": "step"})

    assert "faceted" in result["note"]


async def test_multi_file_output_is_written_and_the_requested_format_is_primary(
    tmp_path: Path,
) -> None:
    fake = FakeZoo()
    fake.outputs = {
        "part.gltf": base64.b64encode(b'{"asset":{}}').decode(),
        "part.bin": base64.b64encode(b"\x00\x01").decode(),
    }
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "part.step")

    result = await server.handle_convert({"file_path": rel, "output_format": "gltf"})

    assert result["file_path"].endswith("/part.gltf")
    assert sorted(f["format"] for f in result["files"]) == ["bin", "gltf"]


async def test_an_output_name_that_would_escape_is_refused(tmp_path: Path) -> None:
    fake = FakeZoo()
    fake.outputs = {"../../etc/evil.step": base64.b64encode(b"x").decode()}
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "part.step")

    # The base name is kept and written inside the operation's own directory.
    result = await server.handle_convert({"file_path": rel, "output_format": "step"})
    assert result["file_path"] == f"output/zoo/{OP_ID}/evil.step"
    assert not (tmp_path.parent / "etc").exists()

    with pytest.raises(ZooError):
        safe_output_name("..")


async def test_a_path_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "secret.step").write_bytes(b"x")
    server = make_server(FakeZoo(), workspace)

    for bad in ("../secret.step", str(tmp_path / "secret.step")):
        with pytest.raises(ValueError, match="outside the adapter workspace"):
            await server.handle_convert({"file_path": bad, "output_format": "stl"})


async def test_missing_and_empty_files_are_refused_before_any_upload(tmp_path: Path) -> None:
    fake = FakeZoo()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="no file"):
        await server.handle_convert({"file_path": "input/none.step", "output_format": "stl"})
    rel = put(tmp_path, "empty.step", b"")
    with pytest.raises(ValueError, match="empty"):
        await server.handle_convert({"file_path": rel, "output_format": "stl"})
    assert fake.requests == []


async def test_unsupported_output_format_is_refused(tmp_path: Path) -> None:
    server = make_server(FakeZoo(), tmp_path)
    rel = put(tmp_path, "part.step")
    with pytest.raises(ValueError, match="unsupported output_format"):
        await server.handle_convert({"file_path": rel, "output_format": "3mf"})


@pytest.mark.parametrize(
    ("name", "declared", "expected"),
    [
        ("a.STEP", None, "step"),
        ("a.stp", None, "step"),
        ("a.x_t", None, "parasolid"),
        ("a.CATPart", None, "catia"),
        ("a.ipt", None, "inventor"),
        ("a.glb", None, "gltf"),
        ("a.prt", "nx", "nx"),
        ("a.dat", "STEP", "step"),
    ],
)
def test_format_detection(name: str, declared: str | None, expected: str) -> None:
    assert detect_format(Path(name), declared) == expected


@pytest.mark.parametrize(
    ("name", "declared", "match"),
    [
        ("a.prt", None, "NX or Creo"),
        ("a.iges", None, "cannot tell the format"),
        ("a.step", "dwg", "unsupported src_format"),
    ],
)
def test_format_detection_refuses_what_it_cannot_know(
    name: str, declared: str | None, match: str
) -> None:
    with pytest.raises(ZooError, match=match):
        detect_format(Path(name), declared)


async def test_get_conversion_refuses_a_malformed_operation_id(tmp_path: Path) -> None:
    fake = FakeZoo()
    server = make_server(fake, tmp_path)
    with pytest.raises(ValueError, match="not a Zoo operation id"):
        await server.handle_get_conversion({"operation_id": "../../x"})
    assert fake.requests == []


def test_a_sync_response_of_the_older_single_output_shape_is_read() -> None:
    operation = parse_operation(
        {
            "id": OP_ID,
            "status": "completed",
            "output_format": "stl",
            "output": base64.b64encode(b"solid").decode(),
        }
    )
    assert operation.outputs == {"output.stl": b"solid"}


# ---------------------------------------------------------------------------
# Mass properties
# ---------------------------------------------------------------------------


async def test_mass_properties_measures_every_property_in_one_unit(tmp_path: Path) -> None:
    fake = FakeZoo()
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "bracket.step")

    result = await server.handle_mass_properties({"file_path": rel, "material_density": 2700})

    assert result["units"] == {"length": "mm", "area": "mm2", "volume": "mm3", "mass": "g"}
    assert result["volume"] == 12000.0
    assert result["surface_area"] == 3400.0
    assert result["center_of_mass"] == {"x": 10.0, "y": 5.0, "z": 2.5}
    assert result["bounding_box"]["dimensions"] == {"x": 20.0, "y": 10.0, "z": 5.0}
    assert result["mass"] == 32.4
    assert result["material_density_kg_m3"] == 2700.0

    by_path = {r.url.path: r for r in fake.requests}
    assert by_path["/file/volume"].url.params["output_unit"] == "mm3"
    assert by_path["/file/surface-area"].url.params["output_unit"] == "mm2"
    assert by_path["/file/center-of-mass"].url.params["output_unit"] == "mm"
    mass = by_path["/file/mass"].url.params
    assert mass["src_format"] == "step"
    assert mass["material_density"] == "2700.0"
    assert mass["material_density_unit"] == "kg:m3"
    assert mass["output_unit"] == "g"


async def test_mass_properties_without_density_skips_mass(tmp_path: Path) -> None:
    fake = FakeZoo()
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "bracket.step")

    result = await server.handle_mass_properties({"file_path": rel, "length_unit": "in"})

    assert "mass" not in result
    assert result["units"] == {"length": "in", "area": "in2", "volume": "in3"}
    assert "/file/mass" not in {r.url.path for r in fake.requests}


@pytest.mark.parametrize("density", [0, -5, "heavy"])
async def test_mass_properties_rejects_a_bad_density(tmp_path: Path, density: Any) -> None:
    server = make_server(FakeZoo(), tmp_path)
    rel = put(tmp_path, "bracket.step")
    with pytest.raises(ValueError, match="material_density"):
        await server.handle_mass_properties({"file_path": rel, "material_density": density})


# ---------------------------------------------------------------------------
# Transport and errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "refused the API token"),
        (402, "payment method"),
        (413, "too large"),
        (429, "rate-limited"),
    ],
)
async def test_http_errors_raise_with_a_reason(tmp_path: Path, status: int, reason: str) -> None:
    fake = FakeZoo()
    fake.error_status = status
    server = make_server(fake, tmp_path)
    rel = put(tmp_path, "part.step")
    with pytest.raises(ValueError, match=reason):
        await server.handle_convert({"file_path": rel, "output_format": "stl"})


async def test_missing_token_raises_without_calling_zoo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ZOO_API_TOKEN", raising=False)
    fake = FakeZoo()
    server = make_server(fake, tmp_path, token="")
    rel = put(tmp_path, "part.step")
    with pytest.raises(ValueError, match="ZOO_API_TOKEN is unset"):
        await server.handle_convert({"file_path": rel, "output_format": "stl"})
    assert fake.requests == []


def test_base_url_override(monkeypatch) -> None:
    monkeypatch.setenv("ZOO_BASE_URL", "https://proxy.example/zoo/")
    assert ZooClient(api_token="t")._base_url == "https://proxy.example/zoo"


# ---------------------------------------------------------------------------
# Registration, annotations, bootstrap
# ---------------------------------------------------------------------------


def test_registers_three_tools(tmp_path: Path) -> None:
    server = make_server(FakeZoo(), tmp_path)
    assert sorted(server.tool_ids) == ["zoo.convert", "zoo.get_conversion", "zoo.mass_properties"]


def test_conversions_are_writes_and_measuring_is_read_only() -> None:
    for tool_id in ("zoo.convert", "zoo.get_conversion"):
        hints = annotations_for(tool_id)
        assert hints["readOnlyHint"] is False
        assert hints["destructiveHint"] is False
        assert hints["openWorldHint"] is True
    measure = annotations_for("zoo.mass_properties")
    assert measure["readOnlyHint"] is True
    assert measure["openWorldHint"] is True


async def test_bootstrap_registers_zoo_only_with_a_token(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.delenv("ZOO_API_TOKEN", raising=False)
    registry = await bootstrap_tool_registry(adapter_ids=["zoo"])
    assert registry.list_adapters() == []

    monkeypatch.setenv("ZOO_API_TOKEN", "zoo-token")
    registry = await bootstrap_tool_registry(adapter_ids=["zoo"])
    assert [a.adapter_id for a in registry.list_adapters()] == ["zoo"]


async def test_bootstrap_honours_the_per_adapter_toggle(monkeypatch) -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    monkeypatch.setenv("ZOO_API_TOKEN", "zoo-token")
    monkeypatch.setenv("METAFORGE_ADAPTER_ZOO_ENABLED", "false")
    registry = await bootstrap_tool_registry(adapter_ids=["zoo"])
    assert registry.list_adapters() == []
