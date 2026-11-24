"""``photo.measure``: code measures the regions a photo's reader marked (FORGE-925, R3.6).

The reader (the client's model) marks where things are, roughly: the body's box, a
row of pins, each hole, a scale object. Code then finds the real edges inside each
mark, so the pixel numbers that set a size come from the image, not from the model:

* **body**: an Otsu threshold inside the marked box (plus a margin) separates the part
  from its background (the polarity is the one the box's border is not); the part's
  pixels give its oriented length and width (along its own principal axes, robust to
  stray pixels at the 0.5 and 99.5 percentiles), its angle and its four corners;
* **holes** (or pads): the blob under each marked point gives its centre and equivalent
  diameter; consecutive centres give the pitch;
* **rows** (pin rows, a header, a scale object): the intensity profile along the row's
  long axis is split into its periodic features, giving their count, span and pitch.

A reader's own pixel number for a region is a cross-check: a measure more than
:data:`AGREE` from it lowers that region's confidence and says so. Everything is in
pixels of the stored image; ``scale.resolve`` turns pixels into millimetres.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np

#: A reader's pixel number further than this (a fraction) from the measure disagrees.
AGREE = 0.05
#: Margin added around a marked box, as a fraction of its size, so edges are inside it.
MARGIN = 0.08


class MeasureError(ValueError):
    """A region that cannot be measured; the message says why."""


def load_grey(data: bytes) -> np.ndarray[Any, Any]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("L"), dtype=np.float64)


def _box(
    region: dict[str, Any], shape: tuple[int, ...], margin: float = MARGIN
) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` of a marked box ``[x, y, w, h]``, with a margin, inside the image."""
    box = region.get("box")
    if not (isinstance(box, list | tuple) and len(box) == 4):
        raise MeasureError("a region needs box [x, y, width, height] in pixels")
    x, y, w, h = (float(v) for v in box)
    if w <= 1 or h <= 1:
        raise MeasureError("a region's box must be wider and taller than one pixel")
    mx, my = w * margin, h * margin
    height, width = shape[:2]
    x0, y0 = max(0, int(x - mx)), max(0, int(y - my))
    x1, y1 = min(width, int(round(x + w + mx))), min(height, int(round(y + h + my)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise MeasureError("the region's box lies outside the image")
    return x0, y0, x1, y1


def otsu(values: np.ndarray[Any, Any]) -> tuple[float, float]:
    """Otsu's threshold over 0..255 values and its separation (0 none, 1 perfect)."""
    hist, edges = np.histogram(values, bins=256, range=(0, 256))
    total = hist.sum()
    if total == 0:
        return 128.0, 0.0
    prob = hist / total
    omega = np.cumsum(prob)
    mu = np.cumsum(prob * np.arange(256))
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu_t * omega - mu) ** 2 / (omega * (1 - omega))
    between = np.nan_to_num(between)
    k = int(np.argmax(between))
    variance = float(values.var())
    separation = float(between[k] / variance) if variance > 0 else 0.0
    return float(edges[k + 1]), min(1.0, separation)


def foreground(crop: np.ndarray[Any, Any]) -> tuple[np.ndarray[Any, Any], float]:
    """The part's pixels in ``crop``: the Otsu class the crop's border mostly is not."""
    threshold, separation = otsu(crop)
    bright = crop > threshold
    border = np.concatenate([bright[0, :], bright[-1, :], bright[:, 0], bright[:, -1]])
    mask = ~bright if border.mean() > 0.5 else bright
    return mask, separation


def _agreement(measured: float, reader: Any, label: str, notes: list[str]) -> float:
    """A confidence factor: 1 when the reader agrees (or gave nothing), lower otherwise."""
    if not isinstance(reader, int | float) or isinstance(reader, bool) or reader <= 0:
        return 1.0
    gap = abs(measured - float(reader)) / measured if measured else 1.0
    if gap > AGREE:
        notes.append(
            f"{label}: the reader said {float(reader):.1f} px, code measured {measured:.1f} px "
            f"({gap:.0%} apart)"
        )
        return 0.7
    return 1.0


@dataclass
class Body:
    length_px: float
    width_px: float
    angle_deg: float
    corners: list[list[float]]
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "length_px": round(self.length_px, 2),
            "width_px": round(self.width_px, 2),
            "angle_deg": round(self.angle_deg, 2),
            "corners": [[round(x, 1), round(y, 1)] for x, y in self.corners],
            "confidence": round(self.confidence, 2),
        }


def _extent(values: np.ndarray[Any, Any]) -> tuple[float, float]:
    """Where the part starts and ends along one axis, ignoring stray pixels.

    The pixels are counted in one-pixel bins along the axis; the part runs between the
    first and last bins holding at least a fifth of a typical bin. A percentile trim
    would cut a solid part short (half a percent off each end of a uniform run), so
    edges are found by density instead, and each edge bin is counted whole.
    """
    start = float(np.floor(values.min()))
    counts = np.bincount(np.floor(values - start).astype(int))
    typical = float(np.median(counts[counts > 0]))
    full = np.nonzero(counts >= 0.2 * typical)[0]
    return start + float(full[0]), start + float(full[-1]) + 1.0


def _shift_all(mask: np.ndarray[Any, Any], keep: bool) -> np.ndarray[Any, Any]:
    """One 3 x 3 erosion (``keep`` False) or dilation (``keep`` True) step."""
    padded = np.pad(mask, 1, constant_values=keep is False and False)
    out = mask.copy()
    h, w = mask.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            window = padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
            out = (out | window) if keep else (out & window)
    return out


def _component(mask: np.ndarray[Any, Any], seed: tuple[int, int]) -> np.ndarray[Any, Any]:
    """The 4-connected piece of ``mask`` holding ``seed`` (or the largest, if it is off)."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    sizes: list[int] = [0]
    for start in zip(*np.nonzero(mask), strict=True):
        if labels[start]:
            continue
        label = len(sizes)
        labels[start] = label
        stack = [start]
        count = 0
        while stack:
            y, x = stack.pop()
            count += 1
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = label
                    stack.append((ny, nx))
        sizes.append(count)
    if len(sizes) == 1:
        return mask
    chosen = int(labels[seed]) or int(np.argmax(sizes))
    return np.asarray(labels == chosen)


def part_mask(crop: np.ndarray[Any, Any]) -> tuple[np.ndarray[Any, Any], float]:
    """The part alone: foreground, opened to drop thin lines (grid paper, traces), the
    piece at the box's centre, then grown back to its true edges within the foreground."""
    mask, separation = foreground(crop)
    radius = max(2, round(min(crop.shape) * 0.01))
    opened = mask
    for _ in range(radius):
        opened = _shift_all(opened, keep=False)
    if not opened.any():
        return mask, separation
    piece = _component(opened, (crop.shape[0] // 2, crop.shape[1] // 2))
    # Grow back exactly as far as it was eroded: a solid part returns to its own edges,
    # and a thin line crossing them gains no stub.
    for _ in range(radius):
        piece = _shift_all(piece, keep=True) & mask
    return piece, separation


def _hull(points: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    """Convex hull, counter-clockwise (Andrew's monotone chain)."""
    pts = np.unique(points, axis=0)
    if len(pts) < 3:
        return pts
    order = np.lexsort((pts[:, 1], pts[:, 0]))
    pts = pts[order]

    def half(seq: np.ndarray[Any, Any]) -> list[np.ndarray[Any, Any]]:
        out: list[np.ndarray[Any, Any]] = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out

    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def min_area_rect(points: np.ndarray[Any, Any]) -> tuple[float, float, float, list[list[float]]]:
    """``(length, width, angle_deg, corners)`` of the smallest rectangle around ``points``.

    The rectangle's side lies along one of the convex hull's edges (rotating calipers),
    so it finds a turned square's orientation, which principal axes cannot: a square's
    spread is the same in every direction. Lengths count each edge pixel whole.
    """
    hull = _hull(points)
    best: tuple[float, float, float, float, float, float, float] | None = None
    for i in range(len(hull)):
        edge = hull[(i + 1) % len(hull)] - hull[i]
        norm = float(np.hypot(edge[0], edge[1]))
        if norm == 0:
            continue
        ux, uy = edge / norm
        along = hull @ np.array([ux, uy])
        across = hull @ np.array([-uy, ux])
        lo_a, hi_a, lo_c, hi_c = along.min(), along.max(), across.min(), across.max()
        area = (hi_a - lo_a) * (hi_c - lo_c)
        if best is None or area < best[0]:
            best = (area, ux, uy, lo_a, hi_a, lo_c, hi_c)
    assert best is not None
    _area, ux, uy, lo_a, hi_a, lo_c, hi_c = best
    size_a, size_c = hi_a - lo_a + 1.0, hi_c - lo_c + 1.0
    u, v = np.array([ux, uy]), np.array([-uy, ux])
    if size_c > size_a:  # length is the longer side
        size_a, size_c, u, v = size_c, size_a, v, -u
        lo_a, hi_a, lo_c, hi_c = (
            float(np.min(hull @ u)), float(np.max(hull @ u)),
            float(np.min(hull @ v)), float(np.max(hull @ v)),
        )  # fmt: skip
    corners = [
        [float(c) for c in a * u + c2 * v]
        for a, c2 in ((lo_a, lo_c), (hi_a, lo_c), (hi_a, hi_c), (lo_a, hi_c))
    ]
    angle = float(np.degrees(np.arctan2(u[1], u[0])))
    if angle > 90:
        angle -= 180
    elif angle <= -90:
        angle += 180
    return float(size_a), float(size_c), angle, corners


def outline(points: np.ndarray[Any, Any]) -> tuple[float, float, float, list[list[float]]]:
    """``(length, width, angle_deg, corners)`` of the part's own four-cornered outline.

    In an angled photo a rectangular part is a skewed quadrilateral, which no rectangle
    fits: its corners are the hull points nearest the smallest enclosing rectangle's
    corners, and its length and width the mean of each pair of opposite sides. In a top
    view the quadrilateral is the rectangle.
    """
    rect_length, rect_width, angle, rect = min_area_rect(points)
    hull = _hull(points)
    if len(hull) < 4:
        return rect_length, rect_width, angle, rect
    quad = [hull[int(np.argmin(np.hypot(*(hull - np.array(c)).T)))] for c in rect]
    sides = [float(np.hypot(*(quad[(i + 1) % 4] - quad[i]))) + 1.0 for i in range(4)]
    first, second = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    length, width = max(first, second), min(first, second)
    return length, width, angle, [[float(x), float(y)] for x, y in quad]


def measure_body(grey: np.ndarray[Any, Any], region: dict[str, Any], notes: list[str]) -> Body:
    x0, y0, x1, y1 = _box(region, grey.shape)
    mask, separation = part_mask(grey[y0:y1, x0:x1])
    share = float(mask.mean())
    if not 0.05 <= share <= 0.97:
        raise MeasureError(
            f"the body's box holds {share:.0%} part pixels; mark the part with a little "
            "background around it"
        )
    ys, xs = np.nonzero(mask)
    pts = np.column_stack([xs + x0 + 0.5, ys + y0 + 0.5])
    length, width, angle, corners = outline(pts)
    confidence = 0.5 + 0.45 * separation
    confidence *= _agreement(length, region.get("reader_length_px"), "body length", notes)
    confidence *= _agreement(width, region.get("reader_width_px"), "body width", notes)
    return Body(length, width, angle, corners, confidence)


def _blob(
    grey: np.ndarray[Any, Any], cx: float, cy: float, radius: float
) -> tuple[float, float, float]:
    """``(x, y, diameter)`` of the blob under a point: the class the point is in."""
    r = max(3, int(radius * 1.6))
    x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
    x1, y1 = min(grey.shape[1], int(cx) + r + 1), min(grey.shape[0], int(cy) + r + 1)
    crop = grey[y0:y1, x0:x1]
    threshold, _sep = otsu(crop)
    centre_value = grey[int(cy), int(cx)]
    mask = crop > threshold if centre_value > threshold else crop <= threshold
    # The connected piece holding the marked point (4-neighbour flood fill).
    seed = (int(cy) - y0, int(cx) - x0)
    if not mask[seed]:
        raise MeasureError(f"no feature under the point ({cx:.0f}, {cy:.0f})")
    seen = np.zeros_like(mask)
    stack = [seed]
    seen[seed] = True
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if (
                0 <= ny < mask.shape[0]
                and 0 <= nx < mask.shape[1]
                and mask[ny, nx]
                and not seen[ny, nx]
            ):
                seen[ny, nx] = True
                stack.append((ny, nx))
    ys, xs = np.nonzero(seen)
    area = float(len(xs))
    return (
        float(xs.mean() + x0 + 0.5),
        float(ys.mean() + y0 + 0.5),
        float(2 * np.sqrt(area / np.pi)),
    )


def measure_holes(
    grey: np.ndarray[Any, Any], region: dict[str, Any], notes: list[str]
) -> dict[str, Any]:
    """Centres, diameters and spacing of features the reader pointed at."""
    points = region.get("points")
    if not isinstance(points, list) or not points:
        raise MeasureError("a holes region needs points [[x, y], ...] at each feature's middle")
    radius = float(region.get("radius_px") or 6)
    blobs = [_blob(grey, float(p[0]), float(p[1]), radius) for p in points]
    centres = np.array([[b[0], b[1]] for b in blobs])
    out: dict[str, Any] = {
        "centres": [[round(x, 2), round(y, 2)] for x, y in centres],
        "diameters_px": [round(b[2], 2) for b in blobs],
    }
    if len(blobs) >= 2:
        steps = np.linalg.norm(np.diff(centres, axis=0), axis=1)
        out["pitch_px"] = round(float(steps.mean()), 2)
        out["pitch_spread"] = round(float(steps.std() / steps.mean()), 3) if steps.mean() else None
        out["span_px"] = round(float(np.linalg.norm(centres[-1] - centres[0])), 2)
        out["count"] = len(blobs) - 1
        confidence = 0.9 if (out["pitch_spread"] or 0) < 0.05 else 0.6
        confidence *= _agreement(out["pitch_px"], region.get("reader_pitch_px"), "pitch", notes)
        out["confidence"] = round(confidence, 2)
    return out


def measure_row(
    grey: np.ndarray[Any, Any], region: dict[str, Any], notes: list[str]
) -> dict[str, Any]:
    """The periodic features along a row (pins, a header, a scale object)."""
    x0, y0, x1, y1 = _box(region, grey.shape, margin=0.0)
    crop = grey[y0:y1, x0:x1]
    horizontal = (x1 - x0) >= (y1 - y0)
    profile = crop.mean(axis=0 if horizontal else 1)
    threshold, separation = otsu(profile)
    high = profile > threshold
    # The features are the minority class along the row (pins on a body, holes in a header).
    features = high if high.mean() < 0.5 else ~high
    edges = np.diff(features.astype(int))
    starts: list[int] = [int(i) for i in np.nonzero(edges == 1)[0] + 1]
    ends: list[int] = [int(i) for i in np.nonzero(edges == -1)[0] + 1]
    if features[0]:
        starts.insert(0, 0)
    if features[-1]:
        ends.append(len(features))
    centres = [(s + e) / 2 for s, e in zip(starts, ends, strict=False) if e - s >= 1]
    if len(centres) < 2:
        raise MeasureError("fewer than two features found along the row; mark it more tightly")
    offset = x0 if horizontal else y0
    centres_px = [c + offset for c in centres]
    span = centres_px[-1] - centres_px[0]
    count = len(centres_px) - 1
    expected = region.get("count")
    out: dict[str, Any] = {
        "axis": "x" if horizontal else "y",
        "features": len(centres_px),
        "centres_px": [round(c, 2) for c in centres_px],
        "span_px": round(span, 2),
        "count": count,
        "pitch_px": round(span / count, 2),
    }
    confidence = 0.5 + 0.4 * separation
    if isinstance(expected, int) and not isinstance(expected, bool) and expected != count:
        notes.append(
            f"row: the reader said {expected} pitches, code found {count} "
            f"({len(centres_px)} features)"
        )
        confidence *= 0.6
    confidence *= _agreement(span, region.get("reader_span_px"), "row span", notes)
    out["confidence"] = round(confidence, 2)
    return out


def measure(grey: np.ndarray[Any, Any], regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure every marked region; a region that cannot be measured says why."""
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    for index, region in enumerate(regions):
        kind = str(region.get("kind") or "")
        label = str(region.get("label") or kind or f"region {index}")
        try:
            if kind == "body":
                value: dict[str, Any] = measure_body(grey, region, notes).as_dict()
            elif kind == "holes":
                value = measure_holes(grey, region, notes)
            elif kind == "row":
                value = measure_row(grey, region, notes)
            else:
                raise MeasureError("kind is body, holes or row")
        except MeasureError as exc:
            results.append({"label": label, "kind": kind, "error": str(exc)})
            continue
        results.append({"label": label, "kind": kind, **value})
    return {
        "image_size_px": [int(grey.shape[1]), int(grey.shape[0])],
        "regions": results,
        "notes": notes,
    }
