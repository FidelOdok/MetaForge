"""NVIDIA SimReady packages: search, inspect and licence-gated import (FORGE-711).

The network is a dict of URL -> bytes. Licence, manifest, index and refusal
tests need nothing else. Tests that open a real stage build a small one with
``pxr`` and skip without the 'omniverse-usd' extra, like the converter tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mcp_core.annotations import annotations_for
from tool_registry.tools.simready.adapter import SimReadyServer
from tool_registry.tools.simready.config import SimReadyConfig
from tool_registry.tools.simready.importer import (
    attribution_line,
    component_entry,
    family_for,
    library_name,
    licence_notice,
)
from tool_registry.tools.simready.index import words
from tool_registry.tools.simready.licence import decide, detect_licence_text
from tool_registry.tools.simready.package import (
    FetchError,
    PackageError,
    PackageRef,
    download,
    parse_package_url,
    read_manifest,
    select_items,
)
from twin_core.library.catalog import CatalogError, parse_geometry, parse_specs
from twin_core.library.licences import normalise_licence

COMMIT = "0123456789abcdef0123456789abcdef01234567"
CC_BY_TEXT = (
    'Attribution 4.0 International\n\nCreative Commons Corporation ("Creative Commons") is '
    "not a law firm."
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeFetcher:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    async def get(self, url: str) -> bytes:
        self.calls.append(url)
        if url not in self.files:
            raise FetchError(url, 404)
        return self.files[url]


def _package(
    ref: PackageRef,
    files: dict[str, bytes],
    *,
    licence: str | None = "CC-BY-4.0",
    licence_text: str | None = CC_BY_TEXT,
    root: str | None = None,
) -> dict[str, bytes]:
    """The URLs a package is served at: manifest, bill of materials, roots, files."""
    bom = json.dumps(
        {
            "format_version": "1.0",
            "items": [
                {"relative_path": p, "size": len(b), "hash": {"sha256": _sha(b)}}
                for p, b in files.items()
            ],
        }
    ).encode()
    packaging = {
        "format_version": "1.0",
        "package_id": f"simready.hf.{ref.namespace}.{ref.dataset}.{ref.asset}.1",
        "metadata": [
            {"name": "com.nvidia.simready.packaging.bom.json", "hash": {"sha256": _sha(bom)}}
        ],
        "package_hash": {"sha256": "b" * 64},
    }
    if licence:
        packaging["license"] = licence
    served = {
        ref.url: json.dumps(packaging).encode(),
        ref.file_url(".metadata/com.nvidia.simready.packaging.bom.json"): bom,
        ref.file_url(".metadata/com.nvidia.simready.root_usds.json"): json.dumps(
            {"entries": [root or next(iter(files))]}
        ).encode(),
    }
    if licence_text is not None:
        served[ref.file_url(".metadata/LICENSE.txt")] = licence_text.encode()
    for path, data in files.items():
        served[ref.file_url(path)] = data
    return served


def _ref(namespace: str = "Robotiq-Official", asset: str = "gripper_2F85_a01") -> PackageRef:
    return PackageRef(namespace, "simready-assets", COMMIT, asset)


# ---------------------------------------------------------------------------
# Licences
# ---------------------------------------------------------------------------


class TestLicence:
    @pytest.mark.parametrize(
        ("raw", "spdx"),
        [
            ("CC-BY 4.0", "CC-BY-4.0"),
            ("cc-by-4.0", "CC-BY-4.0"),
            ("Creative Commons Attribution 4.0", "CC-BY-4.0"),
            ("apache-2.0", "Apache-2.0"),
            ("bsd-3-clause", "BSD-3-Clause"),
            ("other", "other"),
            ("", None),
            (None, None),
        ],
    )
    def test_spellings_normalise_to_spdx(self, raw: Any, spdx: str | None) -> None:
        assert normalise_licence(raw) == spdx

    @pytest.mark.parametrize(
        ("text", "spdx"),
        [
            (CC_BY_TEXT, "CC-BY-4.0"),
            ("Apache License\nVersion 2.0, January 2004", "Apache-2.0"),
            (
                "Redistribution and use in source and binary forms ... Neither the name of",
                "BSD-3-Clause",
            ),
            ("Permission is hereby granted, free of charge, to any person", "MIT"),
            ("Terms and Conditions for use of Graphical Documentation", None),
        ],
    )
    def test_licence_text_is_recognised(self, text: str, spdx: str | None) -> None:
        assert detect_licence_text(text) == spdx

    def test_agreeing_accepted_licence_is_accepted_with_its_obligations(self) -> None:
        d = decide({"dataset": "cc-by-4.0", "package": "CC-BY-4.0", "usd_layer": "CC-BY 4.0"})
        assert d.accepted and d.licence == "CC-BY-4.0"
        assert "credit" in d.obligations

    def test_vendor_terms_are_refused(self) -> None:
        """Universal Robots' dataset says 'other': its terms bind whoever opens the file."""
        d = decide({"dataset": "other", "package": None})
        assert not d.accepted and d.licence == "other"

    def test_disagreement_is_refused_even_between_accepted_licences(self) -> None:
        d = decide({"package": "CC-BY-4.0", "usd_layer": "Apache-2.0"})
        assert not d.accepted and "inconsistently" in d.reason

    def test_no_licence_anywhere_is_refused(self) -> None:
        d = decide({"dataset": None, "package": None})
        assert not d.accepted and "no licence" in d.reason


class TestCatalogGeometry:
    def test_usd_geometry_keeps_its_licence_and_digest(self) -> None:
        geo = parse_geometry(
            {"usd_url": "https://x/p.json", "sha256": "A" * 64, "licence": "CC-BY 4.0"}
        )
        assert geo == {"usd_url": "https://x/p.json", "sha256": "a" * 64, "licence": "CC-BY-4.0"}

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ({"usd_url": "u", "sha256": "a" * 64}, "needs the package's licence"),
            ({"usd_url": "u", "sha256": "a" * 64, "licence": "other"}, "not one MetaForge"),
            ({"usd_url": "u", "sha256": "short", "licence": "MIT"}, "sha256"),
        ],
    )
    def test_usd_geometry_without_a_usable_licence_is_refused(
        self, raw: dict[str, str], message: str
    ) -> None:
        with pytest.raises(CatalogError, match=message):
            parse_geometry(raw)


# ---------------------------------------------------------------------------
# Package URLs, manifests and downloads
# ---------------------------------------------------------------------------


class TestPackage:
    def test_a_pinned_url_parses(self) -> None:
        ref = parse_package_url(_ref().url)
        assert ref == _ref()
        assert ref.browse_url.endswith(f"/tree/{COMMIT}/gripper_2F85_a01")

    def test_a_branch_is_refused(self) -> None:
        url = _ref().url.replace(COMMIT, "main")
        with pytest.raises(PackageError, match="not a commit"):
            parse_package_url(url)

    def test_something_else_is_refused(self) -> None:
        with pytest.raises(PackageError, match="package_url must be"):
            parse_package_url("https://example.com/model.usd")

    async def test_manifest_with_a_tampered_bill_of_materials_is_refused(self) -> None:
        ref = _ref()
        served = _package(ref, {"simready_usd/a.usda": b"#usda 1.0"})
        served[ref.file_url(".metadata/com.nvidia.simready.packaging.bom.json")] += b" "
        with pytest.raises(PackageError, match="bill of materials does not match"):
            await read_manifest(ref, FakeFetcher(served))

    async def test_missing_licence_file_is_allowed(self) -> None:
        ref = _ref()
        served = _package(ref, {"simready_usd/a.usda": b"#usda 1.0"}, licence_text=None)
        manifest = await read_manifest(ref, FakeFetcher(served))
        assert manifest.licence_text is None and manifest.licence == "CC-BY-4.0"

    async def test_only_layers_are_selected_unless_textures_are_asked_for(self) -> None:
        ref = _ref()
        files = {
            "simready_usd/a.usd": b"x",
            "simready_usd/payloads/geometries.usd": b"y",
            "simready_usd/Textures/t.png": b"t",
            "simready_usd/.thumbs/256x256/a.usd.png": b"z",
            "simready_usd/source_assets/a.usd": b"s",
        }
        manifest = await read_manifest(ref, FakeFetcher(_package(ref, files)))
        assert [i["relative_path"] for i in select_items(manifest)] == [
            "simready_usd/a.usd",
            "simready_usd/payloads/geometries.usd",
        ]
        with_tex = select_items(manifest, include_textures=True)
        assert "simready_usd/Textures/t.png" in [i["relative_path"] for i in with_tex]

    async def test_a_file_that_differs_from_its_digest_is_refused(self, tmp_path: Path) -> None:
        ref = _ref()
        served = _package(ref, {"simready_usd/a.usda": b"#usda 1.0"})
        fetcher = FakeFetcher(served)
        manifest = await read_manifest(ref, fetcher)
        served[ref.file_url("simready_usd/a.usda")] = b"#usda 1.0 tampered"
        with pytest.raises(PackageError, match="does not match its sha256"):
            await download(manifest, manifest.items, tmp_path, fetcher, max_bytes=10**6)

    async def test_a_path_outside_the_package_is_refused(self, tmp_path: Path) -> None:
        ref = _ref()
        served = _package(ref, {"../../escape.usda": b"x"})
        fetcher = FakeFetcher(served)
        manifest = await read_manifest(ref, fetcher)
        with pytest.raises(PackageError, match="outside itself"):
            await download(manifest, manifest.items, tmp_path / "pkg", fetcher, max_bytes=10**6)

    async def test_an_oversized_selection_is_refused_before_fetching(self, tmp_path: Path) -> None:
        ref = _ref()
        fetcher = FakeFetcher(_package(ref, {"simready_usd/a.usda": b"x" * 100}))
        manifest = await read_manifest(ref, fetcher)
        before = len(fetcher.calls)
        with pytest.raises(PackageError, match="over the"):
            await download(manifest, manifest.items, tmp_path, fetcher, max_bytes=10)
        assert len(fetcher.calls) == before


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def _index_files(cfg: SimReadyConfig) -> dict[str, bytes]:
    tree = {
        "tree": [
            {"path": "submissions/Robotiq-Official/simready-assets", "type": "blob"},
            {"path": "submissions/TeradyneRobotics/UniversalRobots", "type": "blob"},
            {"path": "submissions/nvidia/simready-assets", "type": "blob"},
            {"path": "submissions/README.md", "type": "blob"},
        ]
    }
    old, new = "1" * 40, "2" * 40

    def line(ns: str, ds: str, commit: str, asset: str) -> str:
        return PackageRef(ns, ds, commit, asset).url

    raw = f"{cfg.github_raw}/{cfg.index_repo}/{cfg.index_ref}/submissions"
    return {
        f"{cfg.github_api}/repos/{cfg.index_repo}/git/trees/{cfg.index_ref}?recursive=1": (
            json.dumps(tree).encode()
        ),
        f"{raw}/Robotiq-Official/simready-assets": "\n".join(
            [
                line("Robotiq-Official", "simready-assets", old, "gripper_2F85_a01"),
                line("Robotiq-Official", "simready-assets", new, "gripper_2F85_a01"),
                line("Robotiq-Official", "simready-assets", new, "gripper_HandE_a01"),
                "not a url",
            ]
        ).encode(),
        f"{raw}/TeradyneRobotics/UniversalRobots": line(
            "TeradyneRobotics", "UniversalRobots", new, "ur17g_1300"
        ).encode(),
        f"{raw}/nvidia/simready-assets": line(
            "nvidia", "simready-assets", new, "airSystem_compressor_a01"
        ).encode(),
        f"{cfg.hf_api}/Robotiq-Official/simready-assets": json.dumps(
            {"cardData": {"license": "cc-by-4.0"}}
        ).encode(),
        f"{cfg.hf_api}/TeradyneRobotics/UniversalRobots": json.dumps(
            {"cardData": {"license": "other"}}
        ).encode(),
        f"{cfg.hf_api}/nvidia/simready-assets": json.dumps(
            {"tags": ["license:cc-by-4.0"]}
        ).encode(),
    }


class TestSearch:
    def test_names_split_into_words(self) -> None:
        assert words("airSystem_compressor_a01") == ["air", "system", "compressor", "a", "01"]
        assert words("gripper_HandE") == ["gripper", "hand", "e"]

    async def test_search_finds_the_newest_version_and_names_its_licence(self) -> None:
        cfg = SimReadyConfig(workspace_dir="/nowhere")
        server = SimReadyServer(cfg, FakeFetcher(_index_files(cfg)))
        out = await server.search({"query": "gripper 2F85"})
        assert [r["asset"] for r in out["results"]] == ["gripper_2F85_a01"]
        top = out["results"][0]
        assert top["commit"] == "2" * 40 and top["licence"] == "CC-BY-4.0"
        assert top["licence_accepted"] is True

    async def test_every_word_must_match(self) -> None:
        cfg = SimReadyConfig(workspace_dir="/nowhere")
        server = SimReadyServer(cfg, FakeFetcher(_index_files(cfg)))
        out = await server.search({"query": "gripper"})
        assert [r["asset"] for r in out["results"]] == ["gripper_2F85_a01", "gripper_HandE_a01"]
        out = await server.search({"query": "gripper compressor"})
        assert out["results"] == []

    async def test_a_refused_licence_is_left_out_unless_asked_for(self) -> None:
        cfg = SimReadyConfig(workspace_dir="/nowhere")
        server = SimReadyServer(cfg, FakeFetcher(_index_files(cfg)))
        out = await server.search({"query": "ur17g"})
        assert out["results"] == [] and out["left_out_for_licence"] == 1
        out = await server.search({"query": "ur17g", "include_unaccepted": True})
        assert out["results"][0]["licence"] == "other"
        assert out["results"][0]["licence_accepted"] is False

    async def test_owner_filters_and_the_index_is_fetched_once(self) -> None:
        cfg = SimReadyConfig(workspace_dir="/nowhere")
        fetcher = FakeFetcher(_index_files(cfg))
        server = SimReadyServer(cfg, fetcher)
        out = await server.search({"query": "a01", "owner": "nvidia"})
        assert [r["asset"] for r in out["results"]] == ["airSystem_compressor_a01"]
        await server.search({"query": "gripper"})
        trees = [c for c in fetcher.calls if "/git/trees/" in c]
        assert len(trees) == 1


class TestLibraryEntry:
    def _record(self, **kw: Any) -> dict[str, Any]:
        record = {
            "asset": "gripper_2F85_a01",
            "dataset": "Robotiq-Official/simready-assets",
            "owner": "Robotiq-Official",
            "author": "Robotiq",
            "source_url": _ref().browse_url,
            "package_url": _ref().url,
            "package_sha256": "c" * 64,
            "package_id": "pkg",
            "licence": "CC-BY-4.0",
            "mass_kg": 0.9,
            "mass_is_assumption": False,
            "extents_m": [0.1, 0.05, 0.2],
            "semantic_labels": {"wikidata_class": ["gripper"]},
        }
        record.update(kw)
        record["attribution"] = attribution_line(record)
        return record

    def test_the_entry_passes_the_library_s_own_checks(self) -> None:
        entry = component_entry(self._record(), _ref())
        assert entry["name"] == "simready_gripper_2f85_a01" == library_name(_ref())
        assert entry["family"] == "actuator" and entry["origin"] == "purchased"
        specs = parse_specs(entry["specs"])
        assert specs["mass_kg"].value == 0.9 and specs["mass_kg"].source_kind == "manufacturer_spec"
        assert specs["height_mm"].value == 200.0
        geo = parse_geometry(entry["geometry"])
        assert geo["licence"] == "CC-BY-4.0" and geo["sha256"] == "c" * 64

    def test_a_generic_prop_s_mass_is_an_assumption(self) -> None:
        entry = component_entry(self._record(mass_is_assumption=True), _ref("nvidia", "prop"))
        specs = parse_specs(entry["specs"])
        assert specs["mass_kg"].source_kind == "assumption" and specs["mass_kg"].needs_review

    def test_attribution_names_title_author_source_and_licence(self) -> None:
        line = attribution_line(self._record())
        assert '"gripper_2F85_a01" by Robotiq' in line
        assert "creativecommons.org/licenses/by/4.0" in line and COMMIT in line

    def test_family_from_name_label_or_publisher(self) -> None:
        assert family_for(_ref(asset="motor_a01"), {}) == "motor"
        assert family_for(_ref("FANUC-CORPORATION", "CRX-10iA"), {}) == "actuator"
        assert family_for(_ref("nvidia", "table_a01"), {}) == "other"


class TestLicenceFile:
    def _record(self) -> dict[str, Any]:
        return {
            "asset": "onrobot_rg2",
            "dataset": "onrobot/simready-grippers",
            "owner": "onrobot",
            "author": "OnRobot A/S",
            "licence": "BSD-3-Clause",
            "source_url": "https://huggingface.co/datasets/onrobot/simready-grippers/tree/x/onrobot_rg2",
        }

    def test_the_package_text_is_kept_as_it_is(self) -> None:
        assert licence_notice(self._record(), CC_BY_TEXT) == CC_BY_TEXT

    @pytest.mark.parametrize("text", [None, "", "  \n"])
    def test_a_package_without_text_gets_a_notice_not_an_empty_file(self, text: str | None) -> None:
        notice = licence_notice(self._record(), text)
        assert notice.startswith("SPDX-License-Identifier: BSD-3-Clause\n")
        assert "ships no licence text of its own" in notice
        assert "Copyright holder named by the package: OnRobot A/S" in notice
        assert "https://opensource.org/license/bsd-3-clause" in notice


class TestRegistration:
    def test_reads_are_read_only_and_import_is_a_producing_open_world_write(self) -> None:
        search = annotations_for("simready.search")
        assert search["readOnlyHint"] and search["openWorldHint"]
        imp = annotations_for("simready.import")
        assert not imp["readOnlyHint"] and not imp["destructiveHint"] and imp["openWorldHint"]

    def test_four_tools(self) -> None:
        server = SimReadyServer(SimReadyConfig(workspace_dir="/nowhere"), FakeFetcher({}))
        assert sorted(server.tool_ids) == [
            "simready.attribution",
            "simready.import",
            "simready.inspect",
            "simready.search",
        ]

    async def test_attribution_refuses_a_folder_it_did_not_write(self, tmp_path: Path) -> None:
        server = SimReadyServer(SimReadyConfig(workspace_dir=str(tmp_path)), FakeFetcher({}))
        with pytest.raises(PackageError, match="not a folder simready.import wrote"):
            await server.attribution({"folders": ["../etc"]})


# ---------------------------------------------------------------------------
# A real stage (needs the 'omniverse-usd' extra)
# ---------------------------------------------------------------------------


def _stage_bytes(tmp: Path, *, layer_licence: str = "CC-BY 4.0") -> bytes:
    """A gripper: a body whose mesh repeats its MassAPI mass, a finger, a joint."""
    pytest.importorskip("pxr")
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    path = tmp / "build.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)  # centimetres, to prove the unit change
    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())

    def box(path: str, size: float, at: tuple[float, float, float], mass: float) -> Any:
        xf = UsdGeom.Xform.Define(stage, path)
        xf.AddTranslateOp().Set(Gf.Vec3d(*at))
        UsdPhysics.RigidBodyAPI.Apply(xf.GetPrim())
        UsdPhysics.MassAPI.Apply(xf.GetPrim()).CreateMassAttr(mass)
        mesh = UsdGeom.Mesh.Define(stage, f"{path}/Mesh")
        h = size / 2
        pts = [(x, y, z) for x in (-h, h) for y in (-h, h) for z in (0, size)]
        mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
        mesh.CreateFaceVertexCountsAttr([4] * 6)
        mesh.CreateFaceVertexIndicesAttr(
            [0, 1, 3, 2, 4, 6, 7, 5, 0, 4, 5, 1, 2, 3, 7, 6, 0, 2, 6, 4, 1, 5, 7, 3]
        )
        mesh.CreateDisplayColorAttr([Gf.Vec3f(0.2, 0.4, 0.8)])
        UsdPhysics.MassAPI.Apply(mesh.GetPrim()).CreateMassAttr(mass)
        return xf

    box("/Root/Body", 10.0, (0, 0, 0), 0.8)
    box("/Root/Finger", 2.0, (0, 0, 10.0), 0.1)
    # The finger's top face is red through a subset material, as SimReady
    # parts colour themselves; the rest keeps its display colour.
    red = UsdShade.Material.Define(stage, "/Root/Looks/red")
    surface = UsdShade.Shader.Define(stage, "/Root/Looks/red/Surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 0))
    red.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    finger = UsdGeom.Mesh(stage.GetPrimAtPath("/Root/Finger/Mesh"))
    top = UsdGeom.Subset.CreateGeomSubset(
        finger, "top", UsdGeom.Tokens.face, [1], UsdShade.Tokens.materialBind
    )
    UsdShade.MaterialBindingAPI.Apply(top.GetPrim()).Bind(red)
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/Root/Joints/finger")
    joint.CreateBody0Rel().SetTargets(["/Root/Body"])
    joint.CreateBody1Rel().SetTargets(["/Root/Finger"])
    mat = UsdPhysics.MaterialAPI.Apply(stage.DefinePrim("/Root/Materials/rubber", "Material"))
    mat.CreateStaticFrictionAttr(0.9)
    mat.CreateDynamicFrictionAttr(0.8)
    mat.CreateRestitutionAttr(0.1)
    root.GetPrim().CreateAttribute(
        "semantics:labels:wikidata_class", Sdf.ValueTypeNames.TokenArray
    ).Set(["gripper"])
    stage.GetRootLayer().customLayerData = {
        "SimReady_Metadata": {
            "asset_license": layer_licence,
            "author": "Robotiq",
            "asset_name": "gripper_2F85_a01",
            "validation": {
                "profile": "Robotics-Prop",
                "profile_version": "4.0.0",
                "validated_features": {
                    "2026-10-08": {"FET_003_PHYSX": {"passed": True}, "FET_010": {"passed": False}}
                },
            },
        }
    }
    stage.GetRootLayer().Save()
    return path.read_bytes()


class TestRealStage:
    async def test_import_keeps_layers_measures_mass_once_and_writes_a_preview(
        self, tmp_path: Path
    ) -> None:
        layer = _stage_bytes(tmp_path)
        ref = _ref()
        root = "simready_usd/sm_gripper.usda"
        files = {root: layer, "simready_usd/Textures/t.png": b"png"}
        served = _package(ref, files)
        served["https://huggingface.co/api/datasets/Robotiq-Official/simready-assets"] = json.dumps(
            {"cardData": {"license": "cc-by-4.0"}}
        ).encode()
        ws = tmp_path / "ws"
        server = SimReadyServer(SimReadyConfig(workspace_dir=str(ws)), FakeFetcher(served))

        out = await server.import_({"package_url": ref.url})

        folder = ws / out["folder"]
        assert out["folder"] == f"simready/simready_gripper_2f85_a01-{COMMIT[:8]}"
        assert (folder / "usd" / root).read_bytes() == layer
        assert not (folder / "usd" / "simready_usd/Textures").exists()
        record = json.loads((folder / "simready.json").read_text())["record"]
        assert record["mass_kg"] == pytest.approx(0.9)  # 0.8 + 0.1, not doubled
        assert record["extents_m"] == pytest.approx([0.1, 0.1, 0.12])  # cm -> m
        assert [j["type"] for j in record["joints"]] == ["PhysicsRevoluteJoint"]
        assert record["contact_materials"][0]["static_friction"] == pytest.approx(0.9)
        assert record["semantic_labels"]["wikidata_class"] == ["gripper"]
        assert record["conformance"]["failed"] == ["FET_010"]
        assert record["licence"] == "CC-BY-4.0" and record["package_sha256"] == "b" * 64
        assert (folder / "ATTRIBUTION.txt").read_text().startswith('"gripper_2F85_a01" by Robotiq')
        assert (folder / "LICENSE.txt").read_text() == CC_BY_TEXT

        import trimesh

        glb = trimesh.load(folder / "preview.glb")
        # Y up in millimetres: the 12 cm height of the Z-up stage is the GLB's Y.
        assert glb.extents[1] == pytest.approx(120.0, abs=1e-3)
        colours = {
            tuple(c[:3]) for g in glb.geometry.values() for c in g.visual.face_colors.tolist()
        }
        assert colours == {(51, 102, 204), (255, 0, 0)}  # display colour, subset material
        assert out["preview"]["mesh_count"] == 2

        attr = await server.attribution({"folders": [out["folder"]]})
        assert attr["licences"] == ["CC-BY-4.0"] and "Robotiq" in attr["text"]

    async def test_a_layer_that_disagrees_with_the_manifest_is_not_imported(
        self, tmp_path: Path
    ) -> None:
        layer = _stage_bytes(tmp_path, layer_licence="Apache-2.0")
        ref = _ref()
        served = _package(ref, {"simready_usd/sm_gripper.usda": layer})
        ws = tmp_path / "ws"
        server = SimReadyServer(SimReadyConfig(workspace_dir=str(ws)), FakeFetcher(served))
        with pytest.raises(PackageError, match="inconsistently"):
            await server.import_({"package_url": ref.url})
        assert not any((ws / "simready").iterdir())  # the staging folder is gone

    async def test_inspect_reports_a_refused_licence_without_downloading(
        self, tmp_path: Path
    ) -> None:
        ref = _ref("TeradyneRobotics", "ur17g_1300")
        served = _package(
            ref, {"simready_usd/ur.usda": b"#usda 1.0"}, licence="other", licence_text=None
        )
        fetcher = FakeFetcher(served)
        server = SimReadyServer(SimReadyConfig(workspace_dir=str(tmp_path)), fetcher)
        out = await server.inspect({"package_url": ref.url})
        assert out["licence"]["accepted"] is False and out["licence"]["licence"] == "other"
        assert ref.file_url("simready_usd/ur.usda") not in fetcher.calls
