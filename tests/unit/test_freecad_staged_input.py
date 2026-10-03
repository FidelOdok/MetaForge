"""FORGE-514: a staged work-product file survives repeated meshes.

Live finding (shelf run G7): after ``twin.stage_work_product_file`` and one
``freecad.generate_mesh``, a second mesh failed with ``CAD file not found:
/workspace/<name>.step`` (or a bare node id). The staged file was never
removed (it was still on the shared volume); the caller had re-derived a path
from the mesh output name (``/workspace/<stem>.inp``) or passed the node id.
The adapter now resolves those back to the staged file and, when nothing
matches, names the expected staging path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.twin.blob_stager import make_blob_stager
from tool_registry.tools.freecad import operations as ops_mod
from tool_registry.tools.freecad.operations import FreecadOperations
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

_STEP = b"ISO-10303-21;\nfake\nENDSEC;\n"
_INP = "*NODE\n1, 0, 0, 0\n*ELEMENT, type=C3D4, ELSET=Volume1\n1, 1, 1, 1, 1\n"


def _stage(workspace: Path, node: str, name: str = "bracket.step") -> Path:
    d = workspace / "_staged_work_products" / node
    d.mkdir(parents=True)
    f = d / name
    f.write_bytes(_STEP)
    return f


@pytest.fixture()
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FreecadOperations:
    o = FreecadOperations(work_dir=str(tmp_path))
    monkeypatch.setattr(o, "_require_freecad", lambda: None)
    monkeypatch.setattr(o, "_twin_frame_step", lambda p: (p, False))
    monkeypatch.setattr(ops_mod.shutil, "which", lambda _n: "/usr/bin/gmsh")
    o.gmsh_calls = []  # type: ignore[attr-defined]

    def _run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        o.gmsh_calls.append(cmd)  # type: ignore[attr-defined]
        Path(cmd[cmd.index("-o") + 1]).write_text(_INP, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ops_mod.subprocess, "run", _run)
    return o


def test_stage_then_mesh_twice_leaves_staged_file(ops: FreecadOperations, tmp_path: Path) -> None:
    staged = _stage(tmp_path, str(uuid4()))
    first = ops.generate_mesh(str(staged), 5.0)
    second = ops.generate_mesh(str(staged), 5.0)
    assert staged.read_bytes() == _STEP
    assert first["mesh_file"] == second["mesh_file"] == str(tmp_path / "bracket.inp")


def test_mesh_two_element_sizes(ops: FreecadOperations, tmp_path: Path) -> None:
    staged = _stage(tmp_path, str(uuid4()))
    ops.generate_mesh(str(staged), 10.0)
    ops.generate_mesh(str(staged), 4.0)
    sizes = [c[c.index("-clmax") + 1] for c in ops.gmsh_calls]  # type: ignore[attr-defined]
    assert sizes == ["10.0", "4.0"]
    assert staged.exists()


def test_path_rederived_from_mesh_name_resolves_to_staged(
    ops: FreecadOperations, tmp_path: Path
) -> None:
    staged = _stage(tmp_path, str(uuid4()))
    ops.generate_mesh(str(staged), 5.0)
    # The shape of the live failure: /workspace/<stem>.step never existed.
    result = ops.generate_mesh(str(tmp_path / "bracket.step"), 3.0)
    assert result["mesh_file"] == str(tmp_path / "bracket.inp")
    assert ops.gmsh_calls[-1][1] == str(staged)  # type: ignore[attr-defined]


def test_bare_node_id_resolves_to_staged(ops: FreecadOperations, tmp_path: Path) -> None:
    node = str(uuid4())
    staged = _stage(tmp_path, node)
    assert ops._resolve_cad_input(node) == str(staged)


def test_missing_file_error_names_expected_staging_path(
    ops: FreecadOperations, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError) as exc:
        ops.generate_mesh(str(tmp_path / "gone.step"), 5.0)
    msg = str(exc.value)
    assert str(tmp_path / "gone.step") in msg
    assert str(tmp_path / "_staged_work_products") in msg
    assert "twin.stage_work_product_file" in msg


def test_ambiguous_basename_is_not_guessed(ops: FreecadOperations, tmp_path: Path) -> None:
    _stage(tmp_path, str(uuid4()))
    _stage(tmp_path, str(uuid4()))
    with pytest.raises(FileNotFoundError, match="Ambiguous"):
        ops._resolve_cad_input(str(tmp_path / "bracket.step"))


async def test_stager_path_is_what_the_freecad_adapter_resolves(
    ops: FreecadOperations, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twin-side stager and freecad-side resolver agree on the staging layout."""
    from datetime import UTC, datetime

    monkeypatch.setattr("api_gateway.twin.blob_store.fetch_work_product_blob", lambda _k: _STEP)
    twin = InMemoryTwinAPI.create()
    now = datetime.now(UTC)
    wp = await twin.create_work_product(
        WorkProduct(
            id=uuid4(),
            name="Bracket",
            type=WorkProductType.CAD_MODEL,
            domain="mech",
            file_path="",
            content_hash="h",
            format="step",
            metadata={"original_filename": "bracket.step", "minio_object_key": "k"},
            created_at=now,
            updated_at=now,
            created_by="t",
        )
    )
    staged = await make_blob_stager(twin, workspace_dir=tmp_path)(str(wp.id))
    assert ops._resolve_cad_input(staged["file_path"]) == staged["file_path"]
    assert ops._resolve_cad_input(str(wp.id)) == staged["file_path"]
    ops.generate_mesh(staged["file_path"], 5.0)
    ops.generate_mesh(staged["file_path"], 2.0)
    assert Path(staged["file_path"]).read_bytes() == _STEP
