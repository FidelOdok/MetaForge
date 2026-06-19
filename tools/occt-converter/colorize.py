"""Bake per-part colours into a STEP file via OCCT's XDE ColorTool.

FreeCAD authors geometry headless, where colour is a GUI ``ViewObject`` property
that does not exist (and so is never written to STEP). Colour is therefore added
here, as a generation step that runs after FreeCAD's STEP export: each STEP
product gets a colour written into the file. The result is real, versioned model
data — the viewer renders the model's own STEP colours (no render-time palette).

Colours are assigned by a callable ``color_for(name) -> (r,g,b)|None``. The
default is a material/role heuristic (battery green, motor silver, prop black,
lens glass-blue, PCB green, …) so colours carry engineering meaning; a part whose
name matches nothing is left uncoloured.

CLI:
    python colorize.py in.step out.step
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

# Ordered (substring, rgb-0..1). First match on the lower-cased part name wins,
# so put more-specific keys first (e.g. battery_pack before pack).
_MATERIAL_COLORS: list[tuple[str, tuple[float, float, float]]] = [
    ("battery", (0.20, 0.72, 0.38)),   # Li-ion green
    ("main_pcb", (0.09, 0.45, 0.22)),  # FR4 board green
    ("pcb", (0.09, 0.45, 0.22)),
    ("electronic", (0.16, 0.40, 0.45)),  # ESC / electronics teal
    ("motor", (0.78, 0.79, 0.82)),     # metallic silver
    ("hub", (0.42, 0.43, 0.47)),
    ("prop", (0.10, 0.10, 0.12)),      # propeller black
    ("foot", (0.14, 0.14, 0.15)),      # rubber foot
    ("lens", (0.32, 0.56, 0.86)),      # glass blue
    ("camera", (0.17, 0.17, 0.20)),    # camera body near-black
    ("gimbal", (0.40, 0.41, 0.45)),
    ("arm", (0.56, 0.58, 0.62)),       # structural body gray
    ("fuselage", (0.86, 0.87, 0.89)),  # light body shell
    ("body", (0.82, 0.83, 0.85)),
]


def material_color(name: str) -> tuple[float, float, float] | None:
    """Heuristic material/role colour for a part name, or None if unknown."""
    n = (name or "").lower()
    for key, rgb in _MATERIAL_COLORS:
        if key in n:
            return rgb
    return None


def _label_name(label) -> str:
    """Product name for an XDE label (OCCT exposes it directly)."""
    try:
        return str(label.GetLabelName())
    except Exception:  # noqa: BLE001
        return ""


def colorize_step(
    in_path: str,
    out_path: str,
    color_for: Callable[[str], tuple[float, float, float] | None] = material_color,
) -> int:
    """Read a STEP, set a colour per named product, write a coloured STEP.

    Returns the number of products coloured. Names are preserved.
    """
    from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
    from OCC.Core.STEPControl import STEPControl_AsIs
    from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_DocumentTool

    doc = TDocStd_Document("pythonocc-doc")  # plain string init (no XCAFApp)
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.ReadFile(in_path)
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

    colored = 0

    def visit(label) -> None:
        nonlocal colored
        if shape_tool.IsAssembly(label):
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(label, comps)
            for i in range(1, comps.Length() + 1):
                comp = comps.Value(i)
                ref = TDF_Label()
                if shape_tool.GetReferredShape(comp, ref):
                    visit(ref)
                else:
                    visit(comp)
            return
        rgb = color_for(_label_name(label))
        if rgb is not None:
            color_tool.SetColor(
                label, Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TOC_RGB), XCAFDoc_ColorGen
            )
            colored += 1

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    for i in range(1, roots.Length() + 1):
        visit(roots.Value(i))

    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    writer.Write(out_path)
    return colored


def main() -> None:
    parser = argparse.ArgumentParser(description="Bake per-part colours into a STEP via XDE")
    parser.add_argument("input", help="Input STEP path")
    parser.add_argument("output", help="Output (coloured) STEP path")
    args = parser.parse_args()
    try:
        n = colorize_step(args.input, args.output)
        print(f"coloured {n} products -> {args.output}")
    except Exception as exc:  # noqa: BLE001
        print(f"colorize failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
