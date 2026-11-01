"""Analysis models stay out of the design checks; long labels keep their link.

FORGE-596: a Table run's FEA idealisations (a tabletop on bonded pads, and one
on posts) were committed as cad_models. The G7 gate then counted them as loose
design parts ("no assembly covering ..."), and an assembly grouping them was
flagged for overlapping the real tabletop, which an idealisation does by
design. Coverage was also judged on whichever assembly was listed last.

FORGE-595: the same run's evidence statements ran 500-700 characters. The
project link stores the statement as a label in a 255-character column, so
Postgres refused it and the recorder, treating the link as best-effort, left the
evidence out of the project with only ``project_linked: false`` to show for it.
"""

from __future__ import annotations

import asyncio
import base64
from uuid import UUID

import pytest

from api_gateway.projects.backend import (
    WORK_PRODUCT_LABEL_MAX,
    InMemoryProjectBackend,
    work_product_label,
)
from api_gateway.runs.geometry_constraints import check_assembly, is_analysis_model
from api_gateway.twin.geometry_recorder import make_geometry_recorder

_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
_STEP_B64 = base64.b64encode(_STEP).decode("ascii")


def _box(x0: float, x1: float) -> dict:
    return {"min": [x0, 0, 0], "max": [x1, 10, 10]}


def _part(node_id: str, name: str) -> tuple[str, str, dict]:
    return (node_id, name, {"bbox_mm": _box(0, 10)})


def _assembly(name: str, refs: list[dict]) -> tuple[str, str, dict]:
    return (f"asm-{name}", name, {"parts": refs, "bbox_mm": _box(0, 20)})


# ── FORGE-596: coverage and analysis models ──────────────────────────────


class TestCoverage:
    def test_any_assembly_covering_every_part_is_enough(self) -> None:
        refs = [{"node_id": "a", "position_bbox_mm": _box(0, 10)}]
        top = [
            {"node_id": "a", "position_bbox_mm": _box(0, 10)},
            {"node_id": "b", "position_bbox_mm": _box(10, 20)},
        ]
        parts = [_part("a", "A"), _part("b", "B")]
        # The covering assembly listed first, a sub-assembly last.
        out = check_assembly([*parts, _assembly("table", top), _assembly("sub", refs)])
        assert not [v for v in out.violations if "covering" in v], out.violations

    def test_order_does_not_matter(self) -> None:
        refs = [{"node_id": "a", "position_bbox_mm": _box(0, 10)}]
        top = [
            {"node_id": "a", "position_bbox_mm": _box(0, 10)},
            {"node_id": "b", "position_bbox_mm": _box(10, 20)},
        ]
        parts = [_part("a", "A"), _part("b", "B")]
        first = check_assembly([*parts, _assembly("sub", refs), _assembly("table", top)])
        second = check_assembly([*parts, _assembly("table", top), _assembly("sub", refs)])
        assert first.violations == second.violations == []

    def test_no_assembly_covers_everything_names_what_is_missing(self) -> None:
        parts = [_part("a", "A"), _part("b", "B"), _part("c", "C")]
        out = check_assembly(
            [
                *parts,
                _assembly("one", [{"node_id": "a"}]),
                _assembly("two", [{"node_id": "a"}, {"node_id": "b"}]),
            ]
        )
        assert "multi-part design has no assembly covering: C" in out.violations


def test_an_analysis_model_is_recognised() -> None:
    assert is_analysis_model({"model_role": "analysis"})
    assert not is_analysis_model({"model_role": "design"})
    assert not is_analysis_model({})


class TestCommitGeometryRole:
    @staticmethod
    def _server(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
        import digital_twin.storage.work_product_blobs as blobs
        from tool_registry.tools.twin.adapter import TwinServer
        from twin_core.api import InMemoryTwinAPI

        monkeypatch.setattr(
            blobs,
            "store_work_product_blob",
            lambda nid, fn, content, content_type="": f"work-products/{nid}/{fn}",
        )
        twin = InMemoryTwinAPI.create()
        return twin, TwinServer(twin=twin, geometry_recorder=make_geometry_recorder(twin, None))

    def test_an_analysis_model_records_its_role_and_what_it_idealises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        twin, server = self._server(monkeypatch)
        out = asyncio.run(
            server.commit_geometry(
                {
                    "step_base64": _STEP_B64,
                    "name": "tabletop_fea_pinned_corners",
                    "model_role": "analysis",
                    "idealises": "CAD-TABLETOP",
                }
            )
        )
        wp = asyncio.run(twin.get_work_product(UUID(out["node_id"])))
        assert wp is not None
        assert wp.metadata["model_role"] == "analysis"
        assert wp.metadata["idealises"] == "CAD-TABLETOP"
        assert is_analysis_model(wp.metadata)

    def test_a_design_model_carries_no_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        twin, server = self._server(monkeypatch)
        out = asyncio.run(server.commit_geometry({"step_base64": _STEP_B64, "name": "Tabletop"}))
        wp = asyncio.run(twin.get_work_product(UUID(out["node_id"])))
        assert wp is not None and "model_role" not in wp.metadata

    @pytest.mark.parametrize(
        "args",
        [{"model_role": "fea"}, {"idealises": "CAD-TABLETOP"}],
    )
    def test_bad_role_arguments_are_refused(
        self, monkeypatch: pytest.MonkeyPatch, args: dict
    ) -> None:
        _, server = self._server(monkeypatch)
        with pytest.raises(ValueError):
            asyncio.run(server.commit_geometry({"step_base64": _STEP_B64, "name": "M", **args}))


# ── FORGE-595: long labels ───────────────────────────────────────────────


class TestWorkProductLabel:
    def test_a_short_label_is_unchanged(self) -> None:
        assert work_product_label("Evidence: top FoS 5.9") == "Evidence: top FoS 5.9"

    def test_a_long_label_fits_the_column(self) -> None:
        label = work_product_label("x" * 700)
        assert len(label) == WORK_PRODUCT_LABEL_MAX
        assert label.endswith("…")

    def test_whitespace_is_collapsed(self) -> None:
        assert work_product_label("a\n  b\tc") == "a b c"

    def test_every_backend_stores_the_shortened_label(self) -> None:
        backend = InMemoryProjectBackend()

        async def scenario() -> str:
            project = await backend.create_project(name="Table", description="", status="draft")
            await backend.link_work_product(project.id, "wp-1", "s" * 700, "evidence")
            got = await backend.get_project(project.id)
            assert got is not None
            return got.work_products[-1].name

        assert len(asyncio.run(scenario())) == WORK_PRODUCT_LABEL_MAX
