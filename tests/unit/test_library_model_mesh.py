"""A library part's real model becomes a mesh the studio and viewer draw (2026-11-12).

The owner chose (2026-11-12) to store and show a maker's own model even when no licence is
stated, for internal design work, while the route into engineering tools and deliverables
keeps refusing such a licence. The converter's ``/faces``
route gives each face the colour its STEP states (tools/occt-converter/test_faces.py); these
tests hand the pipeline such groups, so the answer is known.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from api_gateway.library import model_mesh
from twin_core.library.catalog import SqliteCatalog

# A 10 x 20 x 3 mm box, Z up, from (0,0,0) to (10,20,3): 8 corners, 12 triangles.
CORNERS = [(x, y, z) for x in (0, 10) for y in (0, 20) for z in (0, 3)]
FACES = [
    (0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
    (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3),
]  # fmt: skip
BLUE = [0.2745, 0.3333, 0.7647]  # the XIAO's solder mask, as its STEP states it


def group(
    name: str = "body",
    colour: list[float] | None = BLUE,
    source: str = "part",
    shift: tuple[float, float, float] = (0, 0, 0),
) -> dict[str, Any]:
    """One colour group as the converter's /faces returns it: the box, moved by shift."""
    return {
        "name": name,
        "colour": colour,
        "colour_source": source,
        "positions": [c + s for p in CORNERS for c, s in zip(p, shift, strict=True)],
        "indices": [i for f in FACES for i in f],
    }


STEP = b"ISO-10303-21; a stand-in STEP for the tests"


def entry(data: bytes, *, path: str = "part.step", up: str | None = None) -> dict[str, Any]:
    model: dict[str, Any] = {
        "model_id": "maker_step",
        "version": "1",
        "kind": "cad",
        "engine": "step",
        "entrypoint": path,
        "usage": "the maker's model",
        "artifacts": [
            {
                "path": path,
                "url": "https://maker.example/part.zip",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "licence": "none stated (internal use only)",
                "source_revision": "2026-11-12",
            }
        ],
    }
    if up:
        model["up_axis"] = up
    return {"name": "part", "version": 3, "models": [model]}


class Recorder:
    def __init__(self, data: bytes) -> None:
        self.data, self.calls = data, 0

    def __call__(self, url: str) -> bytes:
        self.calls += 1
        return self.data


def make(
    e: dict[str, Any], fetch: Any, made: list[dict[str, Any]], tmp_path: Path
) -> dict[str, Any]:
    return model_mesh.make_mesh(e, fetch=fetch, convert=lambda b, n: made, cache_dir=tmp_path)


def test_a_z_up_box_comes_out_y_up_centred_with_its_colour(tmp_path: Path) -> None:
    mesh = make(entry(STEP), Recorder(STEP), [group()], tmp_path)
    assert mesh["size_mm"] == [10.0, 3.0, 20.0]  # length, height (was z), width
    assert mesh["triangles"] == 12
    part = mesh["parts"][0]
    assert part["name"] == "body"
    # The colour is the screen value the STEP states, passed through unchanged.
    assert part["colour"] == [0.275, 0.333, 0.765] and part["colour_source"] == "part"
    ys = part["positions"][1::3]
    assert min(ys) == -1.5 and max(ys) == 1.5
    assert mesh["source"]["licence"].startswith("none stated")


def test_a_y_up_file_is_left_standing(tmp_path: Path) -> None:
    mesh = make(entry(STEP, up="y"), Recorder(STEP), [group()], tmp_path)
    assert mesh["size_mm"] == [10.0, 20.0, 3.0]


def test_each_colour_group_stays_a_part_of_its_own(tmp_path: Path) -> None:
    red = group("body", [0.8431, 0.0588, 0.0588], "face", shift=(0, 0, 10))
    mesh = make(entry(STEP, up="y"), Recorder(STEP), [group(), red], tmp_path)
    assert mesh["size_mm"] == [10.0, 20.0, 13.0]
    assert [p["colour"] for p in mesh["parts"]] == [[0.275, 0.333, 0.765], [0.843, 0.059, 0.059]]
    assert [p["colour_source"] for p in mesh["parts"]] == ["part", "face"]


def test_a_face_with_no_stated_colour_is_left_uncoloured_not_painted_grey(
    tmp_path: Path,
) -> None:
    mesh = make(entry(STEP), Recorder(STEP), [group(colour=None, source="none")], tmp_path)
    assert mesh["parts"][0]["colour"] is None
    assert mesh["parts"][0]["colour_source"] == "none"


def test_a_mesh_made_by_the_old_recipe_is_made_again(tmp_path: Path) -> None:
    old = tmp_path / "meshes" / f"{hashlib.sha256(STEP).hexdigest()}-z-r1.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"recipe": 1, "parts": [{"colour": [0.353, 0.353, 0.353]}]}))
    mesh = make(entry(STEP), Recorder(STEP), [group()], tmp_path)
    assert mesh["recipe"] == model_mesh.RECIPE >= 2
    assert mesh["parts"][0]["colour"] == [0.275, 0.333, 0.765]


def test_the_step_is_taken_from_a_zip_by_its_path(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Read me first.rtf", b"terms")
        zf.writestr("SKRTLAE010/SKRTLA.STEP", STEP)
    data = buf.getvalue()
    seen: list[bytes] = []

    def convert(b: bytes, name: str) -> list[dict[str, Any]]:
        seen.append(b)
        return [group()]

    model_mesh.make_mesh(
        entry(data, path="SKRTLA.STEP"),
        fetch=Recorder(data),
        convert=convert,
        cache_dir=tmp_path,
    )
    assert seen == [STEP]


def test_a_changed_file_is_refused_not_shown(tmp_path: Path) -> None:
    with pytest.raises(model_mesh.MeshError, match="sha256"):
        make(entry(STEP), Recorder(b"X" * len(STEP)), [group()], tmp_path)


def test_the_file_and_the_mesh_are_made_once(tmp_path: Path) -> None:
    fetch = Recorder(STEP)
    first = make(entry(STEP), fetch, [group()], tmp_path)
    second = make(entry(STEP), fetch, [], tmp_path)
    assert first == second and fetch.calls == 1


def test_a_heavy_mesh_is_reduced_to_the_budget() -> None:
    import math

    # A 60 x 60 grid sheet with a bump: 7,200 triangles.
    n = 60
    pts = [(i, j, math.sin(i / 5) * math.cos(j / 5)) for i in range(n + 1) for j in range(n + 1)]
    idx: list[int] = []
    for i in range(n):
        for j in range(n):
            a = i * (n + 1) + j
            idx += [a, a + 1, a + n + 1, a + 1, a + n + 2, a + n + 1]
    out = model_mesh.reduce(
        [{"name": "s", "colour": [1, 1, 1], "points": pts, "indices": idx}], 2000
    )
    assert 0 < sum(len(p["indices"]) // 3 for p in out) <= 2000


def test_an_entry_without_a_step_model_says_so(tmp_path: Path) -> None:
    with pytest.raises(model_mesh.MeshError, match="no active STEP"):
        model_mesh.make_mesh({"models": []}, fetch=Recorder(b""), convert=lambda b, n: [],
                             cache_dir=tmp_path)  # fmt: skip


def test_a_converter_that_is_down_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def refused(*a: Any, **k: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", refused)
    with pytest.raises(model_mesh.MeshError, match="not running"):
        model_mesh.occt_faces(STEP, "x.step")


def test_a_converter_refusal_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def failed(url: str, **k: Any) -> httpx.Response:
        assert url.endswith("/faces") and k["params"] == {"quality": "standard"}
        return httpx.Response(500, json={"error": "Failed to read STEP file"})

    monkeypatch.setattr(httpx, "post", failed)
    with pytest.raises(model_mesh.MeshError, match="Failed to read STEP"):
        model_mesh.occt_faces(STEP, "x.step")


def test_the_library_keeps_a_model_with_no_stated_licence_and_its_up_axis() -> None:
    catalog = SqliteCatalog()
    e = entry(STEP, up="y")
    saved = catalog.register(
        "component",
        {"name": "part", "family": "module", "origin": "purchased", "models": e["models"]},
    ).as_dict()
    assert saved["models"][0]["up_axis"] == "y"
    assert saved["models"][0]["artifacts"][0]["licence"].startswith("none stated")


def test_a_model_published_before_up_axis_keeps_its_exact_content() -> None:
    catalog = SqliteCatalog()
    e = entry(STEP)
    first = catalog.register(
        "component",
        {"name": "part", "family": "module", "origin": "purchased", "models": e["models"]},
    )
    assert "up_axis" not in first.as_dict()["models"][0]
    catalog.update(first.id, {"models": first.as_dict()["models"], "notes": "again"})


async def test_the_resolver_still_refuses_an_unaccepted_licence_for_engineering_use(
    tmp_path: Path,
) -> None:
    from tool_registry.tools.library import model_resolver

    e = entry(STEP)

    class Service:
        async def get_entry(self, kind: str, name: str) -> dict[str, Any]:
            return {"entry": {"models": e["models"]}}

    with pytest.raises(ValueError, match="licence"):
        await model_resolver.resolve(
            Service(),
            {"ref": "part", "model_id": "maker_step", "version": "1", "engine": "step"},
            workspace=tmp_path,
        )


def test_the_route_serves_the_mesh_and_explains_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api_gateway.library import catalog_routes

    monkeypatch.setenv("METAFORGE_LIBRARY_MESH_DIR", str(tmp_path))
    monkeypatch.setattr(model_mesh, "http_fetch", Recorder(STEP))
    monkeypatch.setattr(model_mesh, "occt_faces", lambda b, n: [group()])
    catalog_routes.init_catalog(SqliteCatalog())
    try:
        e = entry(STEP)
        catalog_routes.get_catalog().register(
            "component",
            {"name": "part", "family": "module", "origin": "purchased", "models": e["models"]},
        )
        catalog_routes.get_catalog().register(
            "component", {"name": "bare", "family": "module", "origin": "purchased"}
        )
        app = FastAPI()
        app.include_router(catalog_routes.router)
        client = TestClient(app)
        ok = client.get("/v1/library/components/part/mesh")
        assert ok.status_code == 200 and ok.json()["size_mm"] == [10.0, 3.0, 20.0]
        bare = client.get("/v1/library/components/bare/mesh")
        assert bare.status_code == 422 and "no active STEP" in bare.json()["detail"]
        assert client.get("/v1/library/components/nope/mesh").status_code == 404
    finally:
        catalog_routes.init_catalog(None)
