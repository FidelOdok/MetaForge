"""The CadQuery adapter writes where it can, says when it could not, and validates a board.

Three failures seen together on a release:

* the gateway made ``/workspace/_assemblies/<id>/`` as root and asked the
  adapter (uid 1000) to write the assembly there. ``Assembly.save`` ignores
  the STEP writer's status, so the adapter logged "Assembly created" over an
  empty folder and the gateway then failed to read the file;
* a 2.6 MB board STEP (3.5 MB as base64) was over aiohttp's 1 MiB request
  limit, refused with a plain-text 413, and reported as "Invalid JSON";
* a board's 3D export, a compound of many solids, was judged as one made part.

The container-level proof (a root-owned staging folder on the real shared
volume) is ``tests/integration/test_cadquery_isolation_live.py``.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.cadquery import operations as ops_module
from tool_registry.tools.cadquery.operations import (
    CadqueryOperations,
    OutputPathError,
    OutputWriteError,
    require_writable_folder,
    require_written,
)
from tool_registry.tools.cadquery.validity import INFO, classify, measure

# -- the written-file check ---------------------------------------------------


def test_a_written_file_returns_its_size(tmp_path: Path) -> None:
    out = tmp_path / "a.step"
    out.write_bytes(b"ISO-10303-21;")
    assert require_written(str(out), "t") == len(b"ISO-10303-21;")


def test_a_missing_file_is_an_error_that_names_the_path(tmp_path: Path) -> None:
    out = tmp_path / "never.step"
    with pytest.raises(OutputWriteError) as exc:
        require_written(str(out), "cadquery.create_assembly")
    assert "cadquery.create_assembly" in str(exc.value)
    assert repr(str(out)) in str(exc.value)
    assert "Omit output_path" in str(exc.value)


def test_an_empty_file_is_an_error(tmp_path: Path) -> None:
    out = tmp_path / "empty.step"
    out.write_bytes(b"")
    with pytest.raises(OutputWriteError, match="the file is empty"):
        require_written(str(out), "t")


def test_a_folder_that_does_not_exist_is_named(tmp_path: Path) -> None:
    with pytest.raises(OutputWriteError, match="does not exist"):
        require_written(str(tmp_path / "gone" / "a.step"), "t")


def test_a_folder_this_process_cannot_write_is_refused_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "_assemblies" / "9ece402c23b2"
    folder.mkdir(parents=True)
    real_access = os.access

    def access(path: Any, mode: int) -> bool:
        if Path(path) == folder and mode == os.W_OK:
            return False  # as root-owned 0755 is to uid 1000
        return real_access(path, mode)

    monkeypatch.setattr(ops_module.os, "access", access)
    with pytest.raises(OutputWriteError, match="not writable by this adapter"):
        require_writable_folder(str(folder / "desk-lamp-assembly.step"), "t")


# -- create_assembly against a stub kernel -----------------------------------


class _Assembly:
    """``cq.Assembly`` whose ``save`` behaves as configured."""

    write = True

    def add(self, *_a: Any, **_k: Any) -> None:
        return None

    def save(self, path: str) -> None:
        # Like OCCT's writer on a folder it cannot write: no exception.
        if type(self).write:
            Path(path).write_bytes(b"ISO-10303-21; assembly")


class _Importers:
    @staticmethod
    def importStep(_path: str) -> object:  # noqa: N802
        return object()


class _StubCq:
    Assembly = _Assembly
    importers = _Importers()


@pytest.fixture
def stub_kernel(monkeypatch: pytest.MonkeyPatch) -> type[_Assembly]:
    monkeypatch.setattr(ops_module, "HAS_CADQUERY", True)
    monkeypatch.setattr(ops_module, "cq", _StubCq)
    monkeypatch.setattr(ops_module, "shape_properties", lambda _s: {"volume_mm3": 100.0})
    monkeypatch.setattr(ops_module, "_part_location", lambda _loc: None)
    monkeypatch.setattr(ops_module, "_pairwise_interferences", lambda _a, _n: [])
    _Assembly.write = True
    return _Assembly


PARTS = [{"name": "base", "file": "/workspace/_staged_work_products/x/base.step"}]


def test_without_an_output_path_the_assembly_goes_to_a_folder_of_its_own(
    tmp_path: Path, stub_kernel: type[_Assembly]
) -> None:
    ops = CadqueryOperations(work_dir=str(tmp_path))
    first = ops.create_assembly(PARTS)
    second = ops.create_assembly(PARTS)
    path = Path(first["assembly_file"])
    assert path.is_file() and path.stat().st_size > 0
    assert first["file_size_bytes"] == path.stat().st_size
    assert path.parent.parent == tmp_path / "output" / "cadquery" / "assembly"
    # A new folder each call: two builds never share a file.
    assert first["assembly_file"] != second["assembly_file"]


def test_an_assembly_that_was_not_written_fails_loudly(
    tmp_path: Path, stub_kernel: type[_Assembly], capsys: pytest.CaptureFixture[str]
) -> None:
    stub_kernel.write = False
    ops = CadqueryOperations(work_dir=str(tmp_path))
    with pytest.raises(OutputWriteError, match="was not written"):
        ops.create_assembly(PARTS)
    # "created" is never logged without the file.
    assert "Assembly created" not in capsys.readouterr().out


def test_an_unwritable_named_folder_is_refused_before_the_build(
    tmp_path: Path, stub_kernel: type[_Assembly], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "_assemblies" / "9ece402c23b2"
    folder.mkdir(parents=True)
    real_access = os.access
    monkeypatch.setattr(
        ops_module.os,
        "access",
        lambda p, m: False if Path(p) == folder else real_access(p, m),
    )
    saved: list[str] = []
    monkeypatch.setattr(_Assembly, "save", lambda _self, p: saved.append(p))
    ops = CadqueryOperations(work_dir=str(tmp_path))
    with pytest.raises(OutputWriteError, match="not writable"):
        ops.create_assembly(PARTS, output_path=str(folder / "desk-lamp-assembly.step"))
    assert saved == []


def test_a_named_output_path_outside_the_work_folder_is_refused(
    tmp_path: Path, stub_kernel: type[_Assembly]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    ops = CadqueryOperations(work_dir=str(work))
    with pytest.raises(OutputPathError):
        ops.create_assembly(PARTS, output_path=str(tmp_path / "elsewhere.step"))
    with pytest.raises(OutputPathError):
        ops.create_assembly(PARTS, output_path="../escape.step")


def test_a_named_output_path_inside_the_work_folder_is_written(
    tmp_path: Path, stub_kernel: type[_Assembly]
) -> None:
    ops = CadqueryOperations(work_dir=str(tmp_path))
    out = ops.create_assembly(PARTS, output_path="output/assembly_x.step")
    assert out["assembly_file"] == str(tmp_path / "output" / "assembly_x.step")
    assert Path(out["assembly_file"]).is_file()


# -- a board's 3D export ------------------------------------------------------

BOARD = {
    "is_valid": False,
    "solid_count": 214,
    "closed_solid_count": 190,
    "solid_volumes_mm3": [1500.0] + [2.0] * 213,
    "free_edge_count": 37,
}


def test_a_board_is_one_compound_body_with_its_solid_count() -> None:
    outcome, findings = classify(BOARD, board=True)
    assert outcome == "pass"
    by_check = {f["check"]: f for f in findings}
    assert by_check["solids"]["outcome"] == "pass"
    assert "214 solid(s)" in by_check["solids"]["detail"]
    # Recorded, not decisive.
    for check in ("brep_check", "closed", "free_edges"):
        assert by_check[check]["outcome"] == INFO


def test_the_same_board_judged_as_a_made_part_fails() -> None:
    outcome, _ = classify(BOARD)
    assert outcome == "fail"


@pytest.mark.parametrize(
    "measures",
    [
        {"solid_count": 0, "solid_volumes_mm3": []},
        {"solid_count": 3, "solid_volumes_mm3": [0.0, 0.0, 0.0]},
    ],
)
def test_a_board_with_no_solid_volume_fails(measures: dict[str, Any]) -> None:
    assert classify(measures, board=True)[0] == "fail"


class _Box:
    xmin = ymin = zmin = math.inf
    xmax = ymax = zmax = -math.inf


class _Solid:
    def __init__(self, volume: float) -> None:
        self._v = volume

    def Shells(self) -> list[Any]:  # noqa: N802
        return []

    def Volume(self) -> float:  # noqa: N802
        return self._v


class _Shape:
    def isValid(self) -> bool:  # noqa: N802
        return True

    def Solids(self) -> list[_Solid]:  # noqa: N802
        return [_Solid(10.0), _Solid(math.nan)]

    def BoundingBox(self) -> _Box:  # noqa: N802
        return _Box()


def test_measures_never_carry_nan_or_infinity(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_registry.tools.cadquery import validity

    monkeypatch.setattr(validity, "_free_edge_count", lambda _s: 0)
    out = measure(_Shape())
    assert out["solid_volumes_mm3"] == [10.0]
    assert out["bounding_box"] is None
    # Strict JSON: what a non-Python reader accepts.
    json.dumps(out, allow_nan=False)


# -- the adapter's request size limit ------------------------------------------


def test_the_request_limit_defaults_well_above_a_board_step() -> None:
    from tool_registry.tools.cadquery.entrypoint import max_request_bytes

    assert max_request_bytes({}) == 256 * 1024 * 1024
    assert max_request_bytes({"CADQUERY_MAX_REQUEST_MB": "8"}) == 8 * 1024 * 1024
    assert max_request_bytes({"CADQUERY_MAX_REQUEST_MB": "nonsense"}) == 256 * 1024 * 1024


class _EchoServer:
    def __init__(self) -> None:
        self.bodies: list[int] = []

    async def handle_request(self, body: str) -> str:
        self.bodies.append(len(body))
        return json.dumps({"jsonrpc": "2.0", "id": "1", "result": {"data": {"ok": True}}})


async def test_a_board_sized_request_reaches_the_server() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tool_registry.tools.cadquery.entrypoint import build_app

    server = _EchoServer()
    body = "x" * (4 * 1024 * 1024)  # a 3 MB STEP as base64, and some
    async with TestClient(TestServer(build_app(server))) as client:
        resp = await client.post("/mcp", data=body)
        assert resp.status == 200
        assert (await resp.json())["result"]["data"]["ok"] is True
    assert server.bodies == [len(body)]


async def test_an_oversized_request_is_a_json_rpc_error() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tool_registry.tools.cadquery.entrypoint import build_app

    server = _EchoServer()
    async with TestClient(TestServer(build_app(server, max_bytes=1024))) as client:
        resp = await client.post("/mcp", data="x" * 4096)
        assert resp.status == 413
        payload = json.loads(await resp.text())
    assert "reads at most 1024 bytes" in payload["error"]["data"]["details"]
    assert server.bodies == []


async def test_the_http_transport_names_a_non_json_http_error() -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from mcp_core.protocol import McpError
    from mcp_core.transports import HttpTransport

    async def refuse(_request: web.Request) -> web.Response:
        return web.Response(status=413, text="Maximum request body size 1048576 exceeded")

    app = web.Application()
    app.router.add_post("/mcp", refuse)
    async with TestServer(app) as srv:
        transport = HttpTransport(str(srv.make_url("")))
        try:
            with pytest.raises(McpError) as exc:
                await transport.send("{}")
        finally:
            await transport.disconnect()
    assert "HTTP 413" in exc.value.message
    assert "Maximum request body size" in exc.value.message
