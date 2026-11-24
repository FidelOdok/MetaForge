"""``photo.rectify``: positions on a part seen at an angle, in millimetres (R14.4).

A board photographed at an angle is a quadrilateral, not a rectangle, so a pixel
scale read on one edge is wrong on the other and positions in between are skewed.
Its four corners (``photo.measure`` gives them) fix that: a homography maps the
quadrilateral onto the unit square, where straight lines stay straight and ratios
along each side are true. Two scale references on different sides, one along the
length and one along the width (a 2.54 mm header span, a ruler, a known part),
then give the board's size in millimetres, and every feature's position follows.

With fewer than two such references the board's size along one side is not known:
the result says so and every position is marked ``approximate``, never presented
as measured.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: A reference whose endpoints run more than this far off its side's direction (in
#: rectified, unit-square terms) is not along that side.
_ALONG_TOLERANCE = 0.15


class RectifyError(ValueError):
    """The corners or references cannot be used; the message says why."""


def _point(where: str, raw: Any) -> tuple[float, float]:
    if (
        not isinstance(raw, list | tuple)
        or len(raw) != 2
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in raw)
    ):
        raise RectifyError(f"{where} must be [x, y] in pixels")
    return float(raw[0]), float(raw[1])


def order_corners(corners: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Top-left, top-right, bottom-right, bottom-left as the image shows them."""
    pts = np.asarray(corners, dtype=float)
    centre = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0])
    ring = pts[np.argsort(angles)]  # clockwise on screen (y grows down)
    start = int(np.argmin(ring.sum(axis=1)))  # smallest x + y is the top-left
    ring = np.roll(ring, -start, axis=0)
    return [(float(x), float(y)) for x, y in ring]


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    """The 3x3 projective map taking four ``src`` points onto ``dst`` (DLT)."""
    rows = []
    rhs = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.extend([u, v])
    try:
        h = np.linalg.solve(np.asarray(rows, dtype=float), np.asarray(rhs, dtype=float))
    except np.linalg.LinAlgError as exc:
        raise RectifyError("the four corners are degenerate (three in a line?)") from exc
    return np.append(h, 1.0).reshape(3, 3)


def apply(h: np.ndarray, point: tuple[float, float]) -> tuple[float, float]:
    x, y, w = h @ np.array([point[0], point[1], 1.0])
    return float(x / w), float(y / w)


def _convex(corners: list[tuple[float, float]]) -> bool:
    pts = np.asarray(corners)
    signs = []
    for i in range(4):
        a, b, c = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        signs.append(np.sign((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])))
    return len(set(signs)) == 1 and 0 not in signs


def rectify(arguments: dict[str, Any]) -> dict[str, Any]:
    """Board size and feature positions in millimetres, from an angled view."""
    raw = arguments.get("corners_px")
    if not isinstance(raw, list) or len(raw) != 4:
        raise RectifyError("corners_px is the part's four corners, [[x, y], ...] in pixels")
    corners = order_corners([_point(f"corners_px[{i}]", c) for i, c in enumerate(raw)])
    if not _convex(corners):
        raise RectifyError("the four corners do not make a convex outline")
    h = homography(corners, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])

    # The image's top edge (TL-TR) is the part's length unless the caller says the
    # long side runs the other way.
    length_axis = 0 if str(arguments.get("length_along") or "top_edge") == "top_edge" else 1
    scale: dict[str, list[float]] = {"length": [], "width": []}
    used: list[dict[str, Any]] = []
    notes: list[str] = []
    for i, ref in enumerate(arguments.get("references") or []):
        if not isinstance(ref, dict):
            raise RectifyError(f"references[{i}] must be an object")
        a = apply(h, _point(f"references[{i}].from_px", ref.get("from_px")))
        b = apply(h, _point(f"references[{i}].to_px", ref.get("to_px")))
        mm = ref.get("length_mm")
        if not isinstance(mm, int | float) or isinstance(mm, bool) or mm <= 0:
            raise RectifyError(f"references[{i}].length_mm must be a positive number")
        du, dv = abs(b[0] - a[0]), abs(b[1] - a[1])
        if dv <= _ALONG_TOLERANCE * max(du, 1e-9):
            unit_axis, span = 0, du
        elif du <= _ALONG_TOLERANCE * max(dv, 1e-9):
            unit_axis, span = 1, dv
        else:
            notes.append(f"references[{i}] runs diagonally across the part; not used")
            continue
        side = "length" if unit_axis == length_axis else "width"
        scale[side].append(float(mm) / span)
        used.append({"index": i, "side": side, "mm": float(mm), "fraction_of_side": round(span, 4)})

    sizes: dict[str, Any] = {}
    for side in ("length", "width"):
        if scale[side]:
            values = scale[side]
            sizes[side] = {"value_mm": round(float(np.mean(values)), 3), "references": len(values)}
            if len(values) > 1:
                spread = (max(values) - min(values)) / float(np.mean(values))
                sizes[side]["spread"] = round(spread, 4)
                if spread > 0.02:
                    notes.append(
                        f"the {side} references disagree by {spread:.1%}; check the corners"
                    )
    approximate = not (scale["length"] and scale["width"])
    if approximate:
        # The rectified square has no aspect of its own. Fall back on the outline's
        # mean pixel sides, and say so: under perspective this is a guess.
        tl, tr, br, bl = (np.asarray(c) for c in corners)
        top_bottom = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
        left_right = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
        aspect = (left_right / top_bottom) if length_axis == 0 else (top_bottom / left_right)
        missing = "width" if scale["length"] else "length"
        known = "length" if missing == "width" else "width"
        if scale[known]:
            k = sizes[known]["value_mm"]
            guess = k * aspect if missing == "width" else k / aspect
            sizes[missing] = {"value_mm": round(float(guess), 3), "approximate": True}
        notes.append(
            "two scale references on different sides (one along the length, one along "
            "the width) are needed to correct an angled view; positions are approximate"
        )
    length_mm = (sizes.get("length") or {}).get("value_mm")
    width_mm = (sizes.get("width") or {}).get("value_mm")

    features = []
    for i, feature in enumerate(arguments.get("features_px") or []):
        if not isinstance(feature, dict):
            raise RectifyError(f"features_px[{i}] must be an object")
        u, v = apply(h, _point(f"features_px[{i}].at", feature.get("at")))
        along, across = (u, v) if length_axis == 0 else (v, u)
        out: dict[str, Any] = {
            "name": str(feature.get("name") or f"feature_{i + 1}"),
            "fraction": [round(along, 4), round(1 - across, 4)],
        }
        if length_mm is not None and width_mm is not None:
            # From the lower-left corner, as BOARD positions are given.
            out["x_mm"] = round(along * length_mm, 3)
            out["y_mm"] = round((1 - across) * width_mm, 3)
        out["approximate"] = approximate
        if not (-0.02 <= u <= 1.02 and -0.02 <= v <= 1.02):
            out["outside_part"] = True
        features.append(out)

    return {
        "corners_px": [[round(x, 2), round(y, 2)] for x, y in corners],
        "size_mm": {"length": length_mm, "width": width_mm},
        "sizes": sizes,
        "references_used": used,
        "features": features,
        "approximate": approximate,
        "notes": notes,
        "method": "homography from the four corners; scale per side from the references",
    }
