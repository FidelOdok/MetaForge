"""CalculiX result field for the 3D viewer (FORGE-532).

Covers the ``.frd`` mesh/field parser, outer-surface extraction, the
``metaforge.sim_field`` payload builder (size cap + decimation), the
fixture/load markers, and the adapter wiring that returns the payload
alongside the existing numeric summary.

Fixtures (``tests/fixtures/calculix/two_hex_*.frd``) are two C3D8 hexes
along x in the exact ccx 2.20 ASCII layout of the real capture in
``test_calculix_result_parser.py``: 12 nodes, one shared interior face,
so the outer surface is 10 quads = 20 triangles.
"""

from __future__ import annotations

import base64
import gzip
import json
import math
from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.calculix.adapter import CalculixServer
from tool_registry.tools.calculix.config import CalculixConfig
from tool_registry.tools.calculix.field_payload import (
    PAYLOAD_FORMAT,
    FieldPayloadError,
    FrdModel,
    build_field_payload,
    elements_from_inp_mesh,
    extract_surface,
    nodal_fields,
    parse_frd_model,
    von_mises,
)
from tool_registry.tools.calculix.inp_mesh import parse_mesh_inp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "calculix"
STATIC_FRD = FIXTURES / "two_hex_static.frd"
THERMAL_FRD = FIXTURES / "two_hex_thermal.frd"


def _inflate(gz: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(gz))


class TestParseFrdModel:
    def test_reads_nodes_elements_and_blocks(self) -> None:
        model = parse_frd_model(str(STATIC_FRD))
        assert len(model.nodes) == 12
        assert model.nodes[9] == (20.0, 0.0, 0.0)
        assert model.elements == {
            1: (1, [1, 2, 3, 4, 5, 6, 7, 8]),
            2: (1, [2, 9, 10, 3, 6, 11, 12, 7]),
        }
        assert [b.name for b in model.blocks] == ["DISP", "STRESS"]
        stress = model.blocks_named("STRESS")[0]
        assert stress.components == ["SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"]
        assert stress.values[1][0] == pytest.approx(100.0)

    def test_touching_negative_columns_are_sliced_not_split(self, tmp_path: Path) -> None:
        # ccx writes 12-wide columns with no separator before a minus sign.
        frd = tmp_path / "t.frd"
        frd.write_text(
            "    2C                             1                                     1\n"
            " -1         1-1.00000E+00-2.00000E+00-3.00000E+00\n"
            " -3\n"
            " 9999\n",
            encoding="utf-8",
        )
        assert parse_frd_model(str(frd)).nodes[1] == (-1.0, -2.0, -3.0)

    def test_wrapped_connectivity_lines_are_joined(self, tmp_path: Path) -> None:
        # A he20 lists 20 nodes over two -2 lines.
        ids = list(range(1, 21))
        frd = tmp_path / "t.frd"
        frd.write_text(
            "    3C                             1                                     1\n"
            " -1         1    4    0    1\n"
            " -2" + "".join(f"{n:>10d}" for n in ids[:10]) + "\n"
            " -2" + "".join(f"{n:>10d}" for n in ids[10:]) + "\n"
            " -3\n"
            "    2C                             1                                     1\n"
            " -1         1 0.00000E+00 0.00000E+00 0.00000E+00\n"
            " -3\n",
            encoding="utf-8",
        )
        assert parse_frd_model(str(frd)).elements[1] == (4, ids)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_frd_model(str(tmp_path / "nope.frd"))

    def test_no_node_block_raises(self, tmp_path: Path) -> None:
        frd = tmp_path / "t.frd"
        frd.write_text("    1C\n 9999\n", encoding="utf-8")
        with pytest.raises(FieldPayloadError):
            parse_frd_model(str(frd))


class TestNodalFields:
    def test_von_mises_matches_uniaxial_and_pure_shear(self) -> None:
        assert von_mises([100.0, 0, 0, 0, 0, 0]) == pytest.approx(100.0)
        assert von_mises([0, 0, 0, 10.0, 0, 0]) == pytest.approx(10.0 * math.sqrt(3))

    def test_static_fields_keep_the_displacement_vector(self) -> None:
        fields = nodal_fields(parse_frd_model(str(STATIC_FRD)), "static_stress")
        assert set(fields) == {"displacement", "displacement_magnitude", "von_mises"}
        # node 9 is at x=20: dz = -1e-4 * 400 = -0.04, dy = 2e-4.
        assert fields["displacement"][9] == pytest.approx((0.0, 2e-4, -0.04))
        assert fields["displacement_magnitude"][9] == pytest.approx(math.hypot(2e-4, 0.04))
        assert fields["von_mises"][1] == pytest.approx(100.0)

    def test_uses_a_mises_component_when_the_file_has_one(self) -> None:
        model = FrdModel(nodes={1: (0.0, 0.0, 0.0)})
        from tool_registry.tools.calculix.field_payload import FrdResultBlock

        model.blocks.append(FrdResultBlock(name="STRESS", components=["MISES"], values={1: [42.0]}))
        assert nodal_fields(model, "static_stress")["von_mises"] == {1: 42.0}

    def test_modal_takes_the_first_mode_static_the_last_increment(self) -> None:
        from tool_registry.tools.calculix.field_payload import FrdResultBlock

        model = FrdModel(nodes={1: (0.0, 0.0, 0.0)})
        model.blocks.append(FrdResultBlock("DISP", ["D1"], {1: [1.0, 0.0, 0.0]}))
        model.blocks.append(FrdResultBlock("DISP", ["D1"], {1: [2.0, 0.0, 0.0]}))
        assert nodal_fields(model, "modal")["displacement"][1][0] == 1.0
        assert nodal_fields(model, "static_stress")["displacement"][1][0] == 2.0

    def test_thermal_reads_ndtemp(self) -> None:
        fields = nodal_fields(parse_frd_model(str(THERMAL_FRD)), "thermal")
        assert set(fields) == {"temperature"}
        assert fields["temperature"][9] == pytest.approx(75.0)


class TestExtractSurface:
    def test_interior_face_is_dropped(self) -> None:
        model = parse_frd_model(str(STATIC_FRD))
        tris = extract_surface(model.nodes, model.elements)
        assert len(tris) == 20  # 10 outer quads
        shared = {2, 3, 6, 7}
        assert not any(set(t) <= shared for t in tris)

    def test_triangles_wind_outward(self) -> None:
        model = parse_frd_model(str(STATIC_FRD))
        center = (10.0, 5.0, 5.0)
        for a, b, c in extract_surface(model.nodes, model.elements):
            pa, pb, pc = model.nodes[a], model.nodes[b], model.nodes[c]
            u = [pb[i] - pa[i] for i in range(3)]
            v = [pc[i] - pa[i] for i in range(3)]
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            fc = [(pa[i] + pb[i] + pc[i]) / 3 - center[i] for i in range(3)]
            assert sum(n[i] * fc[i] for i in range(3)) > 0

    def test_single_tet_has_four_faces(self) -> None:
        nodes = {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0), 4: (0.0, 0.0, 1.0)}
        # te10: only the 4 corner nodes shape the surface.
        conn = [1, 2, 3, 4, 90, 91, 92, 93, 94, 95]
        assert len(extract_surface(nodes, {1: (6, conn)})) == 4

    def test_inp_fallback_maps_element_types(self, tmp_path: Path) -> None:
        inp = tmp_path / "m.inp"
        inp.write_text(
            "*NODE\n1, 0, 0, 0\n2, 1, 0, 0\n3, 0, 1, 0\n4, 0, 0, 1\n"
            "*ELEMENT, type=C3D4, ELSET=Volume1\n1, 1, 2, 3, 4\n"
            "*ELEMENT, type=T3D2, ELSET=Line1\n2, 1, 2\n",
            encoding="utf-8",
        )
        assert elements_from_inp_mesh(parse_mesh_inp(str(inp))) == {1: (3, [1, 2, 3, 4])}


class TestBuildFieldPayload:
    def test_static_payload_shape_and_ranges(self) -> None:
        payload = build_field_payload(parse_frd_model(str(STATIC_FRD)), "static_stress")
        body = _inflate(payload.gz_bytes)
        assert body["format"] == PAYLOAD_FORMAT
        assert body["version"] == 1
        assert body["vertex_count"] == 12
        assert body["triangle_count"] == 20
        assert len(body["positions"]) == 36
        assert len(body["indices"]) == 60
        assert max(body["indices"]) == 11
        assert len(body["displacement"]) == 36
        vm = body["fields"]["von_mises"]
        assert vm["unit"] == "MPa"
        assert len(vm["values"]) == 12
        assert vm["max"] == pytest.approx(100.0)
        assert vm["min"] == pytest.approx(0.0)
        assert vm["peak"]["position"] == [0.0, 0.0, 0.0]
        assert body["decimation"]["applied"] is False
        assert payload.summary["quantities"] == ["von_mises", "displacement_magnitude"]
        assert payload.summary["ranges"]["von_mises"]["max"] == pytest.approx(100.0)

    def test_thermal_payload_has_temperature_and_no_displacement(self) -> None:
        body = _inflate(build_field_payload(parse_frd_model(str(THERMAL_FRD)), "thermal").gz_bytes)
        assert body["displacement"] is None
        assert list(body["fields"]) == ["temperature"]
        assert body["fields"]["temperature"]["max"] == pytest.approx(75.0)
        assert body["fields"]["temperature"]["unit"] == "C"

    def test_markers_pass_through(self) -> None:
        marker = {"kind": "fixture", "label": "Surface1", "position": [0, 5, 5]}
        payload = build_field_payload(
            parse_frd_model(str(STATIC_FRD)), "static_stress", markers=[marker]
        )
        assert _inflate(payload.gz_bytes)["markers"] == [marker]
        assert payload.summary["marker_count"] == 1

    def test_over_cap_mesh_is_decimated_but_keeps_the_true_peak(self) -> None:
        # A 30x30x2 grid of hexes: big enough to need decimation under a
        # small cap.
        nodes: dict[int, tuple[float, float, float]] = {}
        nid = 0
        index: dict[tuple[int, int, int], int] = {}
        for i in range(31):
            for j in range(31):
                for k in range(3):
                    nid += 1
                    nodes[nid] = (float(i), float(j), float(k))
                    index[(i, j, k)] = nid
        elements: dict[int, tuple[int, list[int]]] = {}
        eid = 0
        for i in range(30):
            for j in range(30):
                for k in range(2):
                    eid += 1
                    c = [
                        index[(i, j, k)],
                        index[(i + 1, j, k)],
                        index[(i + 1, j + 1, k)],
                        index[(i, j + 1, k)],
                        index[(i, j, k + 1)],
                        index[(i + 1, j, k + 1)],
                        index[(i + 1, j + 1, k + 1)],
                        index[(i, j + 1, k + 1)],
                    ]
                    elements[eid] = (1, c)
        from tool_registry.tools.calculix.field_payload import FrdResultBlock

        stress = {n: [float(p[0] * p[1]), 0, 0, 0, 0, 0] for n, p in nodes.items()}
        model = FrdModel(nodes=nodes, elements=elements)
        model.blocks.append(FrdResultBlock("STRESS", ["SXX"], stress))

        full = build_field_payload(model, "static_stress")
        capped = build_field_payload(model, "static_stress", max_bytes=len(full.gz_bytes) // 3)
        body = _inflate(capped.gz_bytes)
        assert len(capped.gz_bytes) <= len(full.gz_bytes) // 3
        assert body["decimation"]["applied"] is True
        assert body["decimation"]["source_triangle_count"] == full.summary["triangle_count"]
        assert body["triangle_count"] < full.summary["triangle_count"]
        # Legend range and peak come from the full field, not the smoothed one.
        assert body["fields"]["von_mises"]["max"] == pytest.approx(900.0)
        assert body["fields"]["von_mises"]["peak"]["position"] == [30.0, 30.0, 0.0]

    def test_impossible_cap_raises(self) -> None:
        with pytest.raises(FieldPayloadError):
            build_field_payload(parse_frd_model(str(STATIC_FRD)), "static_stress", max_bytes=10)

    def test_no_elements_raises(self) -> None:
        model = parse_frd_model(str(STATIC_FRD))
        model.elements = {}
        with pytest.raises(FieldPayloadError, match="connectivity"):
            build_field_payload(model, "static_stress")

    def test_to_result_inlines_base64_and_file(self) -> None:
        payload = build_field_payload(parse_frd_model(str(STATIC_FRD)), "static_stress")
        out = payload.to_result("/workspace/x_field.json.gz")
        assert out["file"] == "/workspace/x_field.json.gz"
        assert out["encoding"] == "gzip"
        assert base64.b64decode(out["base64"]) == payload.gz_bytes
        assert out["size_bytes"] == len(payload.gz_bytes)


_TWO_HEX_INP = """\
*NODE
1, 0, 0, 0
2, 10, 0, 0
3, 10, 10, 0
4, 0, 10, 0
5, 0, 0, 10
6, 10, 0, 10
7, 10, 10, 10
8, 0, 10, 10
9, 20, 0, 0
10, 20, 10, 0
11, 20, 0, 10
12, 20, 10, 10
*ELEMENT, type=CPS4, ELSET=Surface1
101, 1, 4, 8, 5
*ELEMENT, type=CPS4, ELSET=Surface2
102, 9, 10, 12, 11
*ELEMENT, type=C3D8, ELSET=Volume1
1, 1, 2, 3, 4, 5, 6, 7, 8
2, 2, 9, 10, 3, 6, 11, 12, 7
"""


class TestAdapterReturnsField:
    @pytest.fixture
    def server(self, tmp_path: Path) -> CalculixServer:
        return CalculixServer(config=CalculixConfig(work_dir=str(tmp_path)))

    def _stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frd_source: Path
    ) -> tuple[Path, Path]:
        mesh = tmp_path / "beam.inp"
        mesh.write_text(_TWO_HEX_INP, encoding="utf-8")
        frd = tmp_path / "beam_solved.frd"
        frd.write_text(frd_source.read_text(encoding="utf-8"), encoding="utf-8")

        async def _fake_solver(**_kwargs: Any) -> dict[str, Any]:
            return {"solver_time_s": 0.01, "result_files": [str(frd)]}

        monkeypatch.setattr("tool_registry.tools.calculix.adapter.solver_run_fea", _fake_solver)
        return mesh, frd

    async def test_static_run_returns_field_with_markers(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mesh, frd = self._stage(tmp_path, monkeypatch, STATIC_FRD)
        result = await server._execute_solver(
            str(mesh),
            "static_stress",
            {
                "youngs_modulus_mpa": 210000.0,
                "poissons_ratio": 0.3,
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": (0.0, 0.0, -100.0),
            },
        )
        # The existing summary is untouched.
        assert result["max_von_mises"]["global"] == pytest.approx(100.0)
        field = result["field"]
        assert "error" not in field
        assert Path(field["file"]) == tmp_path / "beam_solved_field.json.gz"
        assert Path(field["file"]).read_bytes() == base64.b64decode(field["base64"])
        body = _inflate(base64.b64decode(field["base64"]))
        kinds = {m["kind"]: m for m in body["markers"]}
        assert kinds["fixture"]["label"] == "Surface1"
        assert kinds["fixture"]["position"] == [0.0, 5.0, 5.0]
        assert kinds["load"]["vector"] == [0.0, 0.0, -100.0]
        assert kinds["load"]["position"] == [20.0, 5.0, 5.0]
        assert frd.exists()

    async def test_thermal_run_returns_temperature_field(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mesh, _frd = self._stage(tmp_path, monkeypatch, THERMAL_FRD)
        result = await server._execute_thermal_solver(
            str(mesh),
            {
                "conductivity_w_mm_k": 0.2,
                "heat_source_node_set": "Surface2",
                "power_dissipation_w": 5.0,
                "sink_node_set": "Surface1",
                "sink_temp_c": 20.0,
            },
        )
        field = result["field"]
        assert field["quantities"] == ["temperature"]
        body = _inflate(base64.b64decode(field["base64"]))
        assert {m["kind"] for m in body["markers"]} == {"heat_source", "sink"}
        sink = next(m for m in body["markers"] if m["kind"] == "sink")
        assert sink["value"] == 20.0 and sink["unit"] == "C"

    async def test_a_field_failure_never_fails_the_solve(
        self, server: CalculixServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mesh, _frd = self._stage(tmp_path, monkeypatch, STATIC_FRD)

        def _boom(*_a: Any, **_k: Any) -> Any:
            raise FieldPayloadError("synthetic")

        monkeypatch.setattr("tool_registry.tools.calculix.adapter.build_field_payload", _boom)
        result = await server._execute_solver(
            str(mesh),
            "static_stress",
            {
                "youngs_modulus_mpa": 210000.0,
                "poissons_ratio": 0.3,
                "fixed_node_set": "Surface1",
                "load_node_set": "Surface2",
                "load_force_n": (0.0, 0.0, -100.0),
            },
        )
        assert result["max_von_mises"]["global"] == pytest.approx(100.0)
        assert result["field"] == {"error": "result field not built: synthetic"}
