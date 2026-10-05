"""Material appearance table and STEP colour writer (FORGE-517).

Headless FreeCAD has no ``ViewObject``, so ``Import.export`` writes no colours
and every part rendered uniform grey. Viewer colours come ONLY from the model's
STEP colours (MET-537: no render-time palette), so the colour has to be
authored into the STEP at export time, from the part's recorded material.

This module is pure Python (no FreeCAD import) so it is unit-testable in CI
and ready to move into the materials library (FORGE-445).

* ``lookup_material_rgb`` maps a free-text material string to an RGB triple, or
  ``None`` for an unknown material. Unknown means *no colour*: nothing is
  invented.
* ``apply_step_colours`` injects ``COLOUR_RGB`` / ``STYLED_ITEM`` entities into
  an already written STEP, per part (by PRODUCT name) or for every solid.
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)

RGB = tuple[float, float, float]

# (aliases, rgb 0-1). Matching is a case-insensitive substring test on the
# recorded material string and the FIRST row that matches wins, so specific
# names ("birch plywood") sit above generic ones ("plywood").
MATERIAL_APPEARANCE: tuple[tuple[tuple[str, ...], RGB], ...] = (
    (("birch plywood", "birch ply", "baltic birch"), (0.87, 0.74, 0.54)),
    (("plywood",), (0.80, 0.65, 0.45)),
    (("pine",), (0.85, 0.70, 0.45)),
    (("petg",), (0.30, 0.55, 0.80)),
    (("pla",), (0.90, 0.90, 0.88)),
    (("aluminium", "aluminum", "6061", "7075"), (0.78, 0.79, 0.81)),
    (("stainless",), (0.66, 0.67, 0.69)),
    (("steel",), (0.35, 0.36, 0.38)),
)


def _normalise_rgb(color: object) -> RGB:
    """Accept 0-1 floats or 0-255 ints; return clamped 0-1 floats."""
    if not isinstance(color, (list, tuple)) or len(color) != 3:
        raise ValueError(f"colour must be an [r, g, b] triple, got {color!r}")
    vals = [float(c) for c in color]
    if any(v < 0 for v in vals):
        raise ValueError(f"colour components must be non-negative, got {color!r}")
    if max(vals) > 1.0:
        vals = [v / 255.0 for v in vals]
    if max(vals) > 1.0:
        raise ValueError(f"colour components out of range, got {color!r}")
    return (vals[0], vals[1], vals[2])


def lookup_material_rgb(material: str | None) -> RGB | None:
    """RGB (0-1) for a recorded material string, or ``None`` when unknown."""
    if not material or not isinstance(material, str):
        return None
    text = material.lower()
    for aliases, rgb in MATERIAL_APPEARANCE:
        if any(re.search(rf"(?<![a-z]){re.escape(a)}(?![a-z])", text) for a in aliases):
            return rgb
    return None


def resolve_rgb(color: object = None, material: str | None = None) -> RGB | None:
    """An explicit colour wins over a material; neither (or unknown) is None."""
    if color is not None:
        return _normalise_rgb(color)
    return lookup_material_rgb(material)


_ENTITY_RE = re.compile(r"#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|ENDSEC;)", re.DOTALL)
_TYPE_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*\(", re.DOTALL)
_REF_RE = re.compile(r"#(\d+)")
_SOLID_TYPES = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS")


def _parse(step_text: str) -> dict[int, tuple[str, str]]:
    start = step_text.find("DATA;")
    body = step_text[start:] if start >= 0 else step_text
    out: dict[int, tuple[str, str]] = {}
    for m in _ENTITY_RE.finditer(body):
        tm = _TYPE_RE.match(m.group(2))
        out[int(m.group(1))] = (tm.group(1) if tm else "COMPLEX", m.group(2))
    return out


def _product_solids(ents: dict[int, tuple[str, str]]) -> dict[str, list[int]]:
    """PRODUCT name -> solid entity ids, via the standard definition chain."""
    products = {
        i: (re.match(r"\s*PRODUCT\s*\(\s*'((?:[^']|'')*)'", a) or [None, ""])[1]
        for i, (t, a) in ents.items()
        if t == "PRODUCT"
    }
    pdf_to_prod: dict[int, int] = {}
    for i, (t, a) in ents.items():
        if t.startswith("PRODUCT_DEFINITION_FORMATION"):
            refs = _REF_RE.findall(a)
            if refs and int(refs[-1]) in products:
                pdf_to_prod[i] = int(refs[-1])
    pd_to_prod: dict[int, int] = {}
    for i, (t, a) in ents.items():
        if t == "PRODUCT_DEFINITION":
            for r in _REF_RE.findall(a):
                if int(r) in pdf_to_prod:
                    pd_to_prod[i] = pdf_to_prod[int(r)]
    pds_to_prod: dict[int, int] = {}
    for i, (t, a) in ents.items():
        if t == "PRODUCT_DEFINITION_SHAPE":
            for r in _REF_RE.findall(a):
                if int(r) in pd_to_prod:
                    pds_to_prod[i] = pd_to_prod[int(r)]
    out: dict[str, list[int]] = {}
    for _i, (t, a) in ents.items():
        if t != "SHAPE_DEFINITION_REPRESENTATION":
            continue
        refs = [int(r) for r in _REF_RE.findall(a)]
        if len(refs) < 2 or refs[0] not in pds_to_prod:
            continue
        name = products[pds_to_prod[refs[0]]]
        rep = ents.get(refs[1])
        if rep is None:
            continue
        for r in _REF_RE.findall(rep[1]):
            item = ents.get(int(r))
            if item and item[0] in _SOLID_TYPES:
                out.setdefault(name, []).append(int(r))
    return out


def apply_step_colours(
    step_bytes: bytes,
    default_rgb: RGB | None = None,
    part_rgb: dict[str, RGB] | None = None,
) -> bytes:
    """Return ``step_bytes`` with a ``STYLED_ITEM`` colour on each solid.

    ``part_rgb`` (PRODUCT name, i.e. the FreeCAD Label, to colour) overrides
    ``default_rgb`` for that part. A solid with neither stays uncoloured. If
    nothing can be coloured the input is returned unchanged.
    """
    part_rgb = part_rgb or {}
    if default_rgb is None and not part_rgb:
        return step_bytes
    text = step_bytes.decode("utf-8", errors="replace")
    ents = _parse(text)
    if not ents:
        return step_bytes
    by_product = _product_solids(ents)
    all_solids = sorted(i for i, (t, _a) in ents.items() if t in _SOLID_TYPES)
    solid_rgb: dict[int, RGB] = {}
    if default_rgb is not None:
        for sid in all_solids:
            solid_rgb[sid] = default_rgb
    for name, rgb in part_rgb.items():
        solids = by_product.get(name)
        if not solids:
            logger.warning("step_colour_part_not_found", part=name, known=sorted(by_product))
            continue
        for sid in solids:
            solid_rgb[sid] = rgb
    if not solid_rgb:
        return step_bytes

    # Representation context of the first solid-bearing representation, for the
    # presentation representation that groups the styled items.
    ctx = None
    for t, a in ents.values():
        if t.endswith("SHAPE_REPRESENTATION") and any(
            ents.get(int(r), ("", ""))[0] in _SOLID_TYPES for r in _REF_RE.findall(a)
        ):
            refs = _REF_RE.findall(a)
            ctx = int(refs[-1])
            break

    nxt = max(ents) + 1
    lines: list[str] = []
    styled: list[int] = []
    by_rgb: dict[RGB, int] = {}
    for sid in sorted(solid_rgb):
        rgb = solid_rgb[sid]
        if rgb not in by_rgb:
            r, g, b = rgb
            ids = list(range(nxt, nxt + 7))
            nxt += 7
            lines += [
                f"#{ids[0]} = COLOUR_RGB('',{r:.6f},{g:.6f},{b:.6f});",
                f"#{ids[1]} = FILL_AREA_STYLE_COLOUR('',#{ids[0]});",
                f"#{ids[2]} = FILL_AREA_STYLE('',(#{ids[1]}));",
                f"#{ids[3]} = SURFACE_STYLE_FILL_AREA(#{ids[2]});",
                f"#{ids[4]} = SURFACE_SIDE_STYLE('',(#{ids[3]}));",
                f"#{ids[5]} = SURFACE_STYLE_USAGE(.BOTH.,#{ids[4]});",
                f"#{ids[6]} = PRESENTATION_STYLE_ASSIGNMENT((#{ids[5]}));",
            ]
            by_rgb[rgb] = ids[6]
        lines.append(f"#{nxt} = STYLED_ITEM('',(#{by_rgb[rgb]}),#{sid});")
        styled.append(nxt)
        nxt += 1
    if ctx is not None:
        refs = ",".join(f"#{s}" for s in styled)
        lines.append(
            f"#{nxt} = MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',({refs}),#{ctx});"
        )
    idx = text.rfind("ENDSEC;")
    if idx < 0:
        return step_bytes
    # DATA's ENDSEC is the last one in the file.
    return (text[:idx] + "\n".join(lines) + "\n" + text[idx:]).encode("utf-8")
