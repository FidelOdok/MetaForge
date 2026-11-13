"""The exact rung through KiCad: a part's size from the library's own 3D model (plan C2).

A tiny library is built in a temporary folder, so the tests need no KiCad install. One
test runs against the real KiCad 10 libraries when they are on the machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api_gateway.imagine.formgraph import fg_context
from api_gateway.imagine.kicad_part import StepSize, estimate_step_size, kicad_geometry
from tool_registry.tools.kicad.config import KicadConfig

SYMBOL = """(kicad_symbol_lib
	(version 20241209)
	(generator "test")
	(symbol "{name}"
		(property "Reference" "U" (at 0 0 0))
		(property "Value" "{name}" (at 0 0 0))
		(property "Footprint" "{footprint}" (at 0 0 0))
		(property "Datasheet" "https://example.com/{name}.pdf" (at 0 0 0))
		(symbol "{name}_1_1"
			(pin power_in line (at 0 0 0) (length 2.54)
				(name "VDD" (effects (font (size 1 1))))
				(number "1" (effects (font (size 1 1)))))
			(pin bidirectional line (at 0 2.54 0) (length 2.54)
				(name "SDA" (effects (font (size 1 1))))
				(number "2" (effects (font (size 1 1)))))
		)
	)
)
"""

STEP = """ISO-10303-21;
HEADER;
/* demo.step 3D STEP model
 * This work is licensed under the [Creative Commons CC-BY-SA 4.0 License](https://x)
 */
ENDSEC;
DATA;
#1 = CARTESIAN_POINT('', (-1.25, -1.0, 0.0));
#2 = CARTESIAN_POINT('', (1.25, 1.0, 0.0));
#3 = CARTESIAN_POINT('', (1.25, 1.0, 0.78));
#4 = CARTESIAN_POINT('', (-1.25, -1.0, 0.78));
ENDSEC;
END-ISO-10303-21;
"""


def _library(
    root: Path,
    *,
    name: str = "DEMO280",
    lib: str = "Sensor_Demo",
    footprint: str = "Pkg_LGA:Demo_LGA-8_2x2.5mm",
    with_model: bool = True,
    with_footprint_file: bool = True,
) -> KicadConfig:
    symbols = root / "symbols"
    symbols.mkdir(parents=True, exist_ok=True)
    (symbols / f"{lib}.kicad_sym").write_text(SYMBOL.format(name=name, footprint=footprint))
    if footprint and with_footprint_file:
        flib, fpart = footprint.split(":", 1)
        fdir = root / "footprints" / f"{flib}.pretty"
        fdir.mkdir(parents=True, exist_ok=True)
        model_ref = f"${{KICAD10_3DMODEL_DIR}}/{flib}.3dshapes/{fpart}.step"
        (fdir / f"{fpart}.kicad_mod").write_text(
            f'(footprint "{fpart}" (model "{model_ref}" (offset (xyz 0 0 0))))'
        )
        if with_model:
            mdir = root / "3dmodels" / f"{flib}.3dshapes"
            mdir.mkdir(parents=True, exist_ok=True)
            (mdir / f"{fpart}.step").write_text(STEP)
    return KicadConfig(
        symbol_dir=str(symbols),
        footprint_dir=str(root / "footprints"),
        model_dir=str(root / "3dmodels"),
    )


def _kernel(size: list[float]) -> Any:
    return lambda path: StepSize(size=size, method="cad_kernel")


def _estimate(size: list[float]) -> Any:
    return lambda path: StepSize(size=size, method="text_estimate")


def _problems(geometry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    fg_context.validate_geometry("part", {"geometry": geometry}, errors, [])
    return errors


def test_a_model_measured_by_a_cad_kernel_is_exact_and_the_basis_names_the_file(
    tmp_path: Path,
) -> None:
    config = _library(tmp_path)
    geometry, note = kicad_geometry(
        {"id": "p", "role": "fn:e_th", "kicad": {"symbol": "Sensor_Demo:DEMO280"}},
        config,
        measure=_kernel([2.5, 2.0, 0.78]),
    )
    assert geometry is not None, note
    assert geometry["fidelity"] == "exact"
    assert geometry["envelope"] == [2.5, 2.0, 0.78]
    assert geometry["body"][0]["size"] == [2.5, 2.0, 0.78]
    basis = geometry["fidelity_basis"]
    assert "Demo_LGA-8_2x2.5mm.step" in basis and "bounding box" in basis
    assert "CC-BY-SA 4.0" in basis and "sha256" in basis
    assert str(tmp_path) not in basis, "the basis must not leak a local path"
    assert not _problems(geometry)


def test_a_plain_text_estimate_is_only_an_approximation_and_says_how_it_was_made(
    tmp_path: Path,
) -> None:
    config = _library(tmp_path)
    geometry, _ = kicad_geometry(
        {"id": "p", "role": "other", "kicad": {"symbol": "Sensor_Demo:DEMO280"}},
        config,
        measure=_estimate([2.5, 2.0, 0.8]),
    )
    assert geometry is not None
    assert geometry["fidelity"] == "approximation"
    assert "estimated from the model's coordinates" in geometry["fidelity_basis"]
    assert "not measured by a CAD kernel" in geometry["fidelity_basis"]


def test_the_default_estimate_reads_the_extent_of_the_models_points() -> None:
    result = estimate_step_size_text(STEP)
    assert result == StepSize(size=[2.5, 2.0, 0.78], method="text_estimate")


def estimate_step_size_text(text: str) -> StepSize | None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "m.step"
        path.write_text(text)
        return estimate_step_size(path)


def test_a_part_is_found_by_its_exact_order_code_and_never_by_a_near_one(tmp_path: Path) -> None:
    config = _library(tmp_path)
    found, _ = kicad_geometry(
        {"id": "p", "role": "other", "mpn": "demo280"}, config, measure=_kernel([1, 1, 1])
    )
    assert found is not None and found["fidelity"] == "exact"
    near, note = kicad_geometry(
        {"id": "p", "role": "other", "mpn": "DEMO28"}, config, measure=_kernel([1, 1, 1])
    )
    assert near is None and "no symbol" in note


def test_a_symbol_name_in_two_libraries_is_not_guessed(tmp_path: Path) -> None:
    config = _library(tmp_path, lib="Sensor_A")
    _library(tmp_path, lib="Sensor_B")
    geometry, note = kicad_geometry(
        {"id": "p", "role": "other", "mpn": "DEMO280"}, config, measure=_kernel([1, 1, 1])
    )
    assert geometry is None and "more than one" in note


def test_a_footprint_with_no_model_file_gives_its_name_size_as_an_approximation(
    tmp_path: Path,
) -> None:
    config = _library(tmp_path, with_model=False)
    geometry, _ = kicad_geometry(
        {"id": "p", "role": "fn:e_th", "kicad": {"symbol": "Sensor_Demo:DEMO280"}},
        config,
        measure=_kernel([9, 9, 9]),
    )
    assert geometry is not None
    assert geometry["fidelity"] == "approximation"
    assert geometry["envelope"][:2] == [2.0, 2.5]
    assert "no 3D model file" in geometry["fidelity_basis"]


def test_a_symbol_without_a_footprint_gives_nothing_to_draw(tmp_path: Path) -> None:
    config = _library(tmp_path, footprint="")
    geometry, note = kicad_geometry(
        {"id": "p", "role": "other", "kicad": {"symbol": "Sensor_Demo:DEMO280"}},
        config,
        measure=_kernel([1, 1, 1]),
    )
    assert geometry is None and "footprint" in note


@pytest.mark.parametrize("symbol", ["../../etc:passwd", "Nope:Missing", "nocolon", ""])
def test_a_bad_or_missing_symbol_is_a_note_not_an_exception(tmp_path: Path, symbol: str) -> None:
    config = _library(tmp_path)
    geometry, note = kicad_geometry(
        {"id": "p", "role": "other", "kicad": {"symbol": symbol}},
        config,
        measure=_kernel([1, 1, 1]),
    )
    assert geometry is None and note


def test_a_model_that_cannot_be_measured_falls_back_to_the_footprint(tmp_path: Path) -> None:
    config = _library(tmp_path)
    geometry, _ = kicad_geometry(
        {"id": "p", "role": "other", "kicad": {"symbol": "Sensor_Demo:DEMO280"}},
        config,
        measure=lambda path: None,
    )
    assert geometry is not None and geometry["fidelity"] == "approximation"
    assert "could not be measured" in geometry["fidelity_basis"]


_REAL = Path("/mnt/c/Program Files/KiCad/10.0/share/kicad")


@pytest.mark.skipif(not _REAL.is_dir(), reason="KiCad 10 is not installed here")
def test_the_real_bmp280_model_gives_a_size_close_to_the_cad_kernels() -> None:
    config = KicadConfig(
        symbol_dir=str(_REAL / "symbols"),
        footprint_dir=str(_REAL / "footprints"),
        model_dir=str(_REAL / "3dmodels"),
    )
    geometry, note = kicad_geometry(
        {"id": "bmp", "role": "fn:e_th", "mpn": "BMP280"}, config, measure=estimate_step_size
    )
    assert geometry is not None, note
    # The CAD kernel measured 2.5 x 2.0 x 0.78 mm; the text estimate is allowed 0.05 mm.
    for got, want in zip(geometry["envelope"], [2.5, 2.0, 0.78], strict=True):
        assert abs(got - want) < 0.05, geometry["envelope"]
    assert geometry["fidelity"] == "approximation"
