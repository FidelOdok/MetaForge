"""The render page and its plan: everything that can be checked without a browser.

``render.model_views`` loads a GLB into a local three.js page in headless
Chromium and reads back one PNG per view. The browser half is
:mod:`tool_registry.tools.render.browser`; this module holds the rest:

* :func:`normalize_views`: view specs as the tool takes them (a name, or
  ``{view, projection, section}``) checked and filled with defaults.
* :func:`build_plan`: the JSON the page renders from. Camera directions are
  given in CAD axes (Z up, front looks along +Y) and converted here to the
  page's three.js frame (Y up), so the page does no axis bookkeeping. Each
  orthographic view names the two CAD axes it shows across and up, for the
  dimension overlay.
* :func:`build_page_html`: the page itself. three.js comes from an import
  map that points at files served from the image, never a CDN, so the
  adapter renders with no network.
* :func:`resolve_route`: which local file a page request maps to, refusing
  anything outside the page's own files.
* :func:`decode_png`: a data URL from the page, checked to be a PNG.

Pure: the standard library and pydantic only.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

#: The origin the page is served from. Playwright answers every request to it
#: from local files (``page.route``); nothing is ever fetched from a network.
ORIGIN = "http://render.metaforge.invalid"
STATIC_DIR = Path(__file__).resolve().parent / "static"
THREE_VERSION = "0.169.0"

#: CAD axes, Z up. Each named view: (direction from the model to the camera,
#: the camera's up), both in CAD axes.
_CAD_VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "iso": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
}
#: ``side`` is the right view, the name a person usually means.
_ALIASES = {"side": "right"}
VIEW_NAMES = (*_CAD_VIEWS, "side", "section")
DEFAULT_VIEWS = ("iso", "front", "side", "top")
#: For the dimension overlay: the CAD axes across and up each flat view.
_DIM_AXES: dict[str, tuple[str, str]] = {
    "front": ("x", "z"),
    "back": ("x", "z"),
    "right": ("y", "z"),
    "left": ("y", "z"),
    "top": ("x", "y"),
    "bottom": ("x", "y"),
}
#: A section looks at the cut face from the side that was cut away.
_SECTION_LOOK: dict[str, str] = {"x": "right", "y": "back", "z": "top"}
_AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}

MAX_VIEWS = 8
MIN_SIZE = 128
MAX_SIZE = 2048
DEFAULT_SIZE = 768
#: Each cell of the contact sheet, in pixels.
SHEET_CELL = 512


class SectionSpec(BaseModel):
    """A cut across one CAD axis, at a fraction of the model's extent along it."""

    axis: Literal["x", "y", "z"]
    #: 0 is the model's minimum along the axis, 1 its maximum.
    offset: float = Field(0.5, ge=0.0, le=1.0)


class ViewSpec(BaseModel):
    """One requested view."""

    view: str
    projection: Literal["orthographic", "perspective"] | None = None
    section: SectionSpec | None = None
    label: str | None = Field(None, max_length=60)

    @field_validator("view")
    @classmethod
    def _known(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in VIEW_NAMES:
            raise ValueError(f"unknown view {value!r}; one of {', '.join(VIEW_NAMES)}")
        return name


class Dimensions(BaseModel):
    """Measured overall extents in millimetres, drawn on the views as given."""

    x: float | None = Field(None, ge=0)
    y: float | None = Field(None, ge=0)
    z: float | None = Field(None, ge=0)
    #: Where the numbers came from (``cadquery bounding box``), shown on the image.
    source: str = Field("measured", max_length=80)


class RenderPlan(BaseModel):
    """What the page renders. See :func:`build_plan`."""

    width: int
    height: int
    background: str
    up_axis: Literal["y", "z"]
    unit_scale: float
    grid: bool
    scale_bar: bool
    environment: Literal["neutral", "none"]
    dimensions: Dimensions | None
    title: str
    views: list[dict[str, Any]]
    sheet: dict[str, int] | None


def cad_to_three(v: tuple[float, float, float]) -> list[float]:
    """A CAD vector (Z up) in the page's frame (Y up): ``(x, y, z) -> (x, z, -y)``.

    The page turns a Z-up model upright with the same rotation (-90 degrees
    about X), so a direction named in CAD axes lands where it means.
    """
    x, y, z = v
    return [x, z, -y if y else 0.0]


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def normalize_views(raw: Any) -> list[ViewSpec]:
    """The requested views, checked; the default set when none are given.

    Accepts names (``"iso"``) and objects (``{"view": "section", "section":
    {"axis": "x"}}``). A ``section`` view must say its axis; any other view
    may carry a section too (a cut seen from that view).
    """
    if raw is None or raw == []:
        return [ViewSpec(view=v) for v in DEFAULT_VIEWS]
    if not isinstance(raw, list):
        raise ValueError("views must be a list of view names or view objects")
    if len(raw) > MAX_VIEWS:
        raise ValueError(f"at most {MAX_VIEWS} views per call")
    out: list[ViewSpec] = []
    for item in raw:
        spec = ViewSpec(view=item) if isinstance(item, str) else ViewSpec.model_validate(item)
        if spec.view == "section" and spec.section is None:
            raise ValueError("a section view needs section.axis (x, y or z)")
        out.append(spec)
    return out


def _view_entry(spec: ViewSpec, default_projection: str) -> dict[str, Any]:
    if spec.view == "section":
        assert spec.section is not None
        base = _SECTION_LOOK[spec.section.axis]
    else:
        base = _ALIASES.get(spec.view, spec.view)
    direction, up = _CAD_VIEWS[base]
    projection = spec.projection or ("perspective" if base == "iso" else default_projection)
    entry: dict[str, Any] = {
        "name": spec.view,
        "base": base,
        "direction": cad_to_three(_unit(direction)),
        "up": cad_to_three(up),
        "projection": projection,
        "dim_axes": list(_DIM_AXES[base]) if base in _DIM_AXES else None,
        "section": None,
        "label": spec.label or _label(spec, base),
    }
    if spec.section is not None:
        axis = spec.section.axis
        entry["name"] = (
            f"section_{axis}" if spec.view == "section" else f"{spec.view}_section_{axis}"
        )
        entry["section"] = {
            "axis": axis,
            # Points with a CAD coordinate above the cut are removed: the page
            # keeps the side the plane normal points into, so it is -axis.
            "normal": cad_to_three(tuple(-c for c in _AXIS_VECTORS[axis])),  # type: ignore[arg-type]
            "axis_three": cad_to_three(_AXIS_VECTORS[axis]),
            "offset": spec.section.offset,
        }
    return entry


def _label(spec: ViewSpec, base: str) -> str:
    if spec.section is not None:
        return f"Section {spec.section.axis.upper()} at {round(spec.section.offset * 100)}%"
    if spec.view == "side":
        return "Side"
    return "Iso" if base == "iso" else base.capitalize()


def sheet_layout(count: int, cell: int = SHEET_CELL) -> dict[str, int]:
    """A near-square grid for ``count`` views: columns, rows and cell size."""
    if count < 1:
        raise ValueError("a sheet needs at least one view")
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return {"cols": cols, "rows": rows, "cell": cell, "width": cols * cell, "height": rows * cell}


def build_plan(
    views: list[ViewSpec],
    *,
    width: int = DEFAULT_SIZE,
    height: int = DEFAULT_SIZE,
    projection: str = "orthographic",
    up_axis: str = "z",
    unit_scale: float = 1.0,
    background: str = "#f3f4f6",
    grid: bool = True,
    scale_bar: bool = True,
    environment: str = "neutral",
    dimensions: dict[str, Any] | None = None,
    title: str = "",
    sheet: bool = True,
) -> RenderPlan:
    """The plan the page renders from, every value checked."""
    for name, value in (("width", width), ("height", height)):
        if not isinstance(value, int) or not MIN_SIZE <= value <= MAX_SIZE:
            raise ValueError(
                f"{name} must be a whole number of pixels from {MIN_SIZE} to {MAX_SIZE}"
            )
    if projection not in ("orthographic", "perspective"):
        raise ValueError("projection is orthographic or perspective")
    if up_axis not in ("y", "z"):
        raise ValueError("up_axis is y (glTF) or z (CAD, as the STEP converter writes)")
    if not (isinstance(unit_scale, int | float) and unit_scale > 0):
        raise ValueError("unit_scale must be above zero (model units per millimetre)")
    if not (isinstance(background, str) and len(background) == 7 and background.startswith("#")):
        raise ValueError("background is a #rrggbb colour")
    int(background[1:], 16)
    entries = [_view_entry(v, projection) for v in views]
    return RenderPlan(
        width=width,
        height=height,
        background=background,
        up_axis=up_axis,  # type: ignore[arg-type]
        unit_scale=float(unit_scale),
        grid=bool(grid),
        scale_bar=bool(scale_bar),
        environment="none" if environment == "none" else "neutral",
        dimensions=Dimensions.model_validate(dimensions) if dimensions else None,
        title=(title or "")[:120],
        views=entries,
        sheet=sheet_layout(len(entries)) if sheet else None,
    )


def _script_json(value: Any) -> str:
    """JSON safe to put inside a ``<script>`` element."""
    return (
        json.dumps(value, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def build_page_html() -> str:
    """The render page: an import map to the local three.js and the viewer module.

    The plan is passed at render time (``window.renderViews(plan)``), so one
    page serves every call.
    """
    imports = {
        "three": "/three/build/three.module.js",
        "three/addons/": "/three/examples/jsm/",
    }
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        "<title>MetaForge model views</title>\n"
        "<style>html,body{margin:0;background:#fff}canvas{display:block}</style>\n"
        f'<script type="importmap">{_script_json({"imports": imports})}</script>\n'
        '<script type="module" src="/viewer.js"></script>\n'
        "</head><body></body></html>\n"
    )


#: Files the page may load, by URL path prefix, and the directory each is under.
def resolve_route(path: str, *, three_dir: Path, static_dir: Path = STATIC_DIR) -> Path | None:
    """The local file for a page request path, or ``None`` (the request is refused).

    Only the viewer's own files and the shipped three.js are served; a path
    that climbs out of either directory is refused.
    """
    clean = path.split("?", 1)[0].split("#", 1)[0]
    if clean.startswith("/three/"):
        root, rel = three_dir, clean[len("/three/") :]
    elif clean.startswith("/") and clean.count("/") == 1 and clean.endswith((".js", ".css")):
        root, rel = static_dir, clean[1:]
    else:
        return None
    if not rel or "\\" in rel or any(part in ("", ".", "..") for part in rel.split("/")):
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


CONTENT_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".html": "text/html",
}


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_png(data_url: Any) -> bytes:
    """The PNG bytes of a ``data:image/png;base64,`` URL the page returned."""
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("the page returned no PNG")
    raw = base64.b64decode(data_url[len(prefix) :], validate=True)
    if not raw.startswith(_PNG_SIGNATURE):
        raise ValueError("the page returned something that is not a PNG")
    return raw


def cad_bbox(raw: Any) -> dict[str, float] | None:
    """The page's bounding box (already in CAD axes and millimetres), rounded."""
    if not isinstance(raw, dict):
        return None
    keys = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    try:
        return {k: round(float(raw[k]), 4) for k in keys}
    except (KeyError, TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_VIEWS",
    "MAX_VIEWS",
    "ORIGIN",
    "STATIC_DIR",
    "THREE_VERSION",
    "VIEW_NAMES",
    "Dimensions",
    "RenderPlan",
    "SectionSpec",
    "ViewSpec",
    "build_page_html",
    "build_plan",
    "cad_bbox",
    "cad_to_three",
    "content_type",
    "decode_png",
    "normalize_views",
    "resolve_route",
    "sheet_layout",
]
