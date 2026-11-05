"""Component ids travel through STEP and the OCCT GLB (SYNC-001, SYNC-002).

A CAD revision's ``component_id`` (or an assembly's ``part_component_ids``)
is written into the STEP product names as ``"<name> [CMP-...]"``; the OCCT
converter splits it back out and writes it into each GLB node's extras, which
the viewer reads to select a part of a multi-part model by its component.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from api_gateway.twin import exact_cad as exact_cad_module
from api_gateway.twin.preview_comparison import read_glb_components
from tests.unit.test_exact_cad import (  # noqa: F401 - _isolated is an autouse fixture
    Harness,
    _isolated,
)
from twin_core.component_identity import (
    split_product_name,
    stamp_step_component_ids,
    stamp_step_for_node,
    step_ids_for_node,
)
from twin_core.geometry_state import set_node_metadata

CONVERTER_DIR = Path(__file__).resolve().parents[2] / "tools" / "occt-converter"

SINGLE = b"""ISO-10303-21;
HEADER;
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#7 = PRODUCT('Open CASCADE STEP translator 7.7 1','Open CASCADE STEP translator 7.7 1','',(#8));
#9 = PRODUCT_DEFINITION('design','',#10,#11);
ENDSEC;
END-ISO-10303-21;
"""

ASSEMBLY = b"""ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1 = PRODUCT('enclosure','enclosure','',(#8));
#2 = PRODUCT('lid','lid','',(#8));
#3 = PRODUCT('base','base','',(#8));
#4 = PRODUCT('lid hinge','lid hinge','',(#8));
#5 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('NAUO1','lid','',#1,#2,$);
#6 = NEXT_ASSEMBLY_USAGE_OCCURRENCE('NAUO2','base','',#1,#3,$);
ENDSEC;
END-ISO-10303-21;
"""


def _converter_ids() -> ModuleType:
    """The converter's own helper, loaded from its image directory."""
    spec = importlib.util.spec_from_file_location(
        "occt_component_ids", CONVERTER_DIR / "component_ids.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _names(step: bytes, kind: str = "PRODUCT") -> list[str]:
    import re

    pattern = rf"(?<![A-Z_]){kind}\('(?:[^']|'')*','((?:[^']|'')*)'"
    return re.findall(pattern, step.decode("latin-1"))


# -- STEP product names --------------------------------------------------------


def test_a_single_part_names_its_component_and_its_item() -> None:
    out, n = stamp_step_component_ids(SINGLE, item_name="Base Plate", component_id="CMP-base-plate")
    assert n == 1
    assert _names(out) == ["Base Plate [CMP-base-plate]"]
    # The product id repeats its name, as OCCT writes it.
    assert b"PRODUCT('Base Plate [CMP-base-plate]','Base Plate [CMP-base-plate]'" in out
    # Nothing but the names changed.
    assert out.replace(b"Base Plate [CMP-base-plate]", b"X") == SINGLE.replace(
        b"Open CASCADE STEP translator 7.7 1", b"X"
    )


def test_assembly_members_are_matched_by_exact_name_only() -> None:
    out, n = stamp_step_component_ids(
        ASSEMBLY,
        item_name="Enclosure",
        component_id="CMP-enclosure",
        part_component_ids={"Lid": "CMP-lid", "Base": "CMP-base"},
    )
    names = _names(out)
    assert names == [
        "enclosure [CMP-enclosure]",
        "lid [CMP-lid]",
        "base [CMP-base]",
        # "lid hinge" contains "lid" but is another part: never guessed.
        "lid hinge",
    ]
    assert _names(out, "NEXT_ASSEMBLY_USAGE_OCCURRENCE") == ["lid [CMP-lid]", "base [CMP-base]"]
    assert n == 5


def test_stamping_is_idempotent_and_leaves_unknown_input_alone() -> None:
    once, _ = stamp_step_component_ids(SINGLE, item_name="Plate", component_id="CMP-plate")
    twice, n = stamp_step_component_ids(once, item_name="Plate", component_id="CMP-other")
    assert twice == once and n == 0
    assert stamp_step_component_ids(SINGLE, item_name="Plate") == (SINGLE, 0)
    assert stamp_step_component_ids(b"glTF....", item_name="P", component_id="CMP-p") == (
        b"glTF....",
        0,
    )
    # Not a component id: nothing is written.
    assert stamp_step_component_ids(SINGLE, item_name="P", component_id="plate")[1] == 0


def test_the_round_trip_recovers_every_id() -> None:
    meta = {"component_id": "CMP-enclosure", "part_component_ids": {"lid": "CMP-lid"}}
    assert step_ids_for_node(meta) == ("CMP-enclosure", {"lid": "CMP-lid"})
    out, _ = stamp_step_for_node(ASSEMBLY, name="Enclosure", meta=meta)
    recovered = {split_product_name(name)[1] for name in _names(out)} - {None}
    assert recovered == {"CMP-enclosure", "CMP-lid"}
    assert split_product_name("lid [CMP-lid]") == ("lid", "CMP-lid")
    assert split_product_name("plain") == ("plain", None)


def test_a_real_step_keeps_the_ids_through_the_kernel(tmp_path: Path) -> None:
    """Write with OCCT, stamp, read the product names back with OCCT's XDE reader."""
    cq = pytest.importorskip("cadquery")
    from OCP.STEPCAFControl import STEPCAFControl_Reader  # type: ignore[import-not-found]
    from OCP.TCollection import TCollection_ExtendedString  # type: ignore[import-not-found]
    from OCP.TDataStd import TDataStd_Name  # type: ignore[import-not-found]
    from OCP.TDF import TDF_LabelSequence  # type: ignore[import-not-found]
    from OCP.TDocStd import TDocStd_Document  # type: ignore[import-not-found]
    from OCP.XCAFDoc import XCAFDoc_DocumentTool  # type: ignore[import-not-found]

    assy = cq.Assembly(name="enclosure")
    assy.add(cq.Workplane().box(10, 10, 2), name="lid")
    assy.add(cq.Workplane().box(10, 10, 8), name="base", loc=cq.Location((0, 0, -6)))
    path = tmp_path / "a.step"
    assy.save(str(path))
    stamped, n = stamp_step_component_ids(
        path.read_bytes(),
        item_name="Enclosure",
        part_component_ids={"lid": "CMP-lid", "base": "CMP-base"},
    )
    assert n >= 2
    out = tmp_path / "b.step"
    out.write_bytes(stamped)

    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.ReadFile(str(out))
    reader.Transfer(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    labels = TDF_LabelSequence()
    tool.GetShapes(labels)
    names = set()
    for i in range(1, labels.Length() + 1):
        attr = TDataStd_Name()
        if labels.Value(i).FindAttribute(TDataStd_Name.GetID_s(), attr):
            names.add(attr.Get().ToExtString())
    ids = {split_product_name(name)[1] for name in names} - {None}
    assert ids == {"CMP-lid", "CMP-base"}


async def test_the_exported_step_carries_the_revisions_component_id() -> None:
    h = Harness()
    default = "Open CASCADE STEP translator 7.7 1"
    product = f"#7 = PRODUCT('{default}','{default}','',(#8));"
    v1 = await h.part()
    job = await h.run_build(v1["item_key"], script=product)
    assert job.status == "succeeded", job.error
    node_id = job.result["node_id"]
    await set_node_metadata(h.twin, await h.node(node_id), component_id="CMP-shelf-bracket")
    out = await h.service.export_step(node_id=node_id)
    export = out["step_export"]
    assert export["component_ids_in_step"] == 1
    content = exact_cad_module.read_export_file(export)
    assert content is not None
    assert _names(content) == ["Shelf Bracket [CMP-shelf-bracket]"]
    # The geometry check ran on the stamped file and still passed.
    assert export["round_trip"]["outcome"] == "pass"


# -- the converter --------------------------------------------------------------


def _glb(nodes: list[dict[str, Any]]) -> bytes:
    """A GLB with one triangle per node, each node a translated copy."""
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    ind = struct.pack("<3I", 0, 1, 2)
    binary = pos + ind
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": [{**n, "mesh": 0} for n in nodes],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(ind)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5125, "count": 3, "type": "SCALAR"},
        ],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = (
        struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def test_the_converter_reads_names_the_same_way_the_gateway_writes_them() -> None:
    ids = _converter_ids()
    for name in ("lid [CMP-lid]", "Base plate  [CMP-base-plate]", "plain", "CMP-x", "[CMP-y]"):
        assert ids.split_product_name(name) == split_product_name(name), name


def test_the_converter_writes_ids_into_node_extras_and_keeps_the_mesh() -> None:
    ids = _converter_ids()
    glb = _glb(
        [
            {"name": "lid", "translation": [0, 0, 10]},
            {"name": "base"},
            {"name": "Part_3", "translation": [5, 0, 0]},
        ]
    )
    out, changed = ids.add_node_extras(
        glb,
        {
            "lid": {"component_id": "CMP-lid", "part_name": "lid"},
            "base": {"component_id": "CMP-base", "part_name": "base"},
        },
    )
    assert changed == 2
    assert len(out) % 4 == 0 and struct.unpack_from("<I", out, 8)[0] == len(out)
    (json_len,) = struct.unpack_from("<I", out, 12)
    doc = json.loads(out[20 : 20 + json_len])
    assert [n.get("extras", {}).get("component_id") for n in doc["nodes"]] == [
        "CMP-lid",
        "CMP-base",
        None,
    ]
    # The binary chunk is untouched, so every triangle still reads back.
    groups = {g["component_id"] or g["name"]: g for g in read_glb_components(out)}
    assert set(groups) == {"CMP-lid", "CMP-base", "Part_3"}
    assert groups["CMP-lid"]["tris"][0][0] == (0.0, 0.0, 10.0)
    assert groups["CMP-lid"]["name"] == "lid"
    # A file that is not a GLB is returned unchanged.
    assert ids.add_node_extras(b"nope", {"x": {}}) == (b"nope", 0)


def test_convert_maps_step_product_names_into_glb_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converter end to end, with the OCCT reader and tessellator stubbed."""
    # The converter image's mesh stack (trimesh needs scipy and networkx to fix normals).
    for module in ("trimesh", "scipy", "networkx"):
        pytest.importorskip(module)
    np = pytest.importorskip("numpy")
    monkeypatch.syspath_prepend(str(CONVERTER_DIR))
    sys.modules.pop("convert", None)
    sys.modules.pop("component_ids", None)
    import convert  # type: ignore[import-not-found]

    cube = (
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32),
        np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int32),
    )
    monkeypatch.setattr(
        convert,
        "_read_step_named",
        lambda path: [("s1", "lid [CMP-lid]", None), ("s2", "base [CMP-base]", None)],
    )
    monkeypatch.setattr(convert, "_tessellate", lambda shape, deflection: cube)
    step = tmp_path / "a.step"
    step.write_bytes(b"ISO-10303-21;")
    meta = convert.convert(str(step), "standard", str(tmp_path / "out"))
    assert [(p["name"], p["componentId"]) for p in meta["parts"]] == [
        ("lid", "CMP-lid"),
        ("base", "CMP-base"),
    ]
    glb = (tmp_path / "out" / "model.glb").read_bytes()
    groups = read_glb_components(glb)
    assert {g["component_id"] for g in groups} == {"CMP-lid", "CMP-base"}
