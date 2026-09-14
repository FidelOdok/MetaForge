"""Handler for the author_design_sketch skill."""

from __future__ import annotations

import math
from html import escape

from skill_registry.skill_base import SkillBase

from .schema import AuthorDesignSketchInput, AuthorDesignSketchOutput, Segment

_SVG_SIZE = 260
_PADDING_FRACTION = 0.22

# (segment, x0, y0, x1, y1) -- one entry per segment, chained end-to-end.
_ChainPoint = tuple[Segment, float, float, float, float]


def _chain(segments: list[Segment]) -> list[_ChainPoint]:
    """Walk the segment list end-to-end into 2D points. Starts pointing
    "up" (-90 deg in screen coords) so a simple leg/arm chain reads
    top-to-bottom the way a person would sketch it by hand; each
    subsequent segment bends by its own joint_angle_deg relative to the
    direction the previous one was already pointing."""
    x, y, angle = 0.0, 0.0, -90.0
    out: list[_ChainPoint] = []
    for seg in segments:
        angle += seg.joint_angle_deg
        rad = math.radians(angle)
        x1 = x + seg.length_mm * math.cos(rad)
        y1 = y + seg.length_mm * math.sin(rad)
        out.append((seg, x, y, x1, y1))
        x, y = x1, y1
    return out


def _fit_scale(chains: list[list[_ChainPoint]]) -> tuple[float, float, float]:
    """A single px-per-mm scale + center point fitted across every chain
    passed in, so a before/after pair is drawn honestly at the same
    scale -- a longer "after" segment must look longer, not just say so."""
    xs, ys = [0.0], [0.0]
    for chain in chains:
        for _seg, x0, y0, x1, y1 in chain:
            xs += [x0, x1]
            ys += [y0, y1]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    scale = (_SVG_SIZE * (1 - 2 * _PADDING_FRACTION)) / span
    return scale, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _render_chain_svg(
    chain: list[_ChainPoint], scale: float, cx: float, cy: float, color: str, caption: str
) -> str:
    origin = _SVG_SIZE / 2

    def to_px(x: float, y: float) -> tuple[float, float]:
        return origin + (x - cx) * scale, origin + (y - cy) * scale

    parts = [f'<svg viewBox="0 0 {_SVG_SIZE} {_SVG_SIZE}" width="100%" height="{_SVG_SIZE}">']
    for seg, x0, y0, x1, y1 in chain:
        px0, py0 = to_px(x0, y0)
        px1, py1 = to_px(x1, y1)
        stroke_w = max(3.0, seg.thickness_mm * scale)
        parts.append(
            f'<line x1="{px0:.1f}" y1="{py0:.1f}" x2="{px1:.1f}" y2="{py1:.1f}" '
            f'stroke="{color}" stroke-width="{stroke_w:.1f}" stroke-linecap="round" />'
        )
        parts.append(f'<circle cx="{px0:.1f}" cy="{py0:.1f}" r="3" fill="#1b1d23" />')
        mx, my = (px0 + px1) / 2, (py0 + py1) / 2
        parts.append(
            f'<text x="{mx:.1f}" y="{my:.1f}" font-size="9" fill="#1b1d23" '
            f'text-anchor="middle" dy="-6">{escape(seg.name)} {seg.length_mm:g}mm</text>'
        )
    if chain:
        _, _, _, tx0, ty0 = chain[-1]
        tx, ty = to_px(tx0, ty0)
        parts.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3" fill="{color}" />')
    parts.append(
        f'<text x="{origin}" y="{_SVG_SIZE - 8}" font-size="11" fill="{color}" '
        f'text-anchor="middle" font-weight="600">{escape(caption)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _dimension_rows(before: list[Segment], after: list[Segment]) -> list[tuple[str, ...]]:
    before_by_name = {s.name: s for s in before}
    rows = []
    for seg in after:
        prior = before_by_name.get(seg.name)
        if prior is None:
            rows.append((seg.name, "—", f"{seg.length_mm:g} mm", "—", f"{seg.thickness_mm:g} mm"))
        else:
            rows.append(
                (
                    seg.name,
                    f"{prior.length_mm:g} mm",
                    f"{seg.length_mm:g} mm",
                    f"{prior.thickness_mm:g} mm",
                    f"{seg.thickness_mm:g} mm",
                )
            )
    return rows


def _render_html(
    name: str,
    subject_name: str,
    summary: str,
    before_segments: list[Segment],
    after_segments: list[Segment],
    change_notes: list[str],
) -> str:
    is_revision = bool(before_segments)
    after_chain = _chain(after_segments)
    before_chain = _chain(before_segments) if is_revision else []
    scale, cx, cy = _fit_scale([c for c in (before_chain, after_chain) if c])

    diagrams = ""
    if is_revision:
        diagrams = (
            '<div class="diagram">'
            + _render_chain_svg(before_chain, scale, cx, cy, "#6b7280", "Before")
            + "</div>"
            '<div class="diagram">'
            + _render_chain_svg(after_chain, scale, cx, cy, "#1d4ed8", "After")
            + "</div>"
        )
    else:
        diagrams = (
            '<div class="diagram">'
            + _render_chain_svg(after_chain, scale, cx, cy, "#1d4ed8", "Proposed")
            + "</div>"
        )

    rows = _dimension_rows(before_segments, after_segments)
    if is_revision:
        header = (
            "<tr><th>Segment</th><th>Length before</th><th>Length after</th>"
            "<th>Thickness before</th><th>Thickness after</th></tr>"
        )
        table_rows = "".join(
            f"<tr>{''.join(f'<td>{escape(v)}</td>' for v in r)}</tr>" for r in rows
        )
    else:
        header = "<tr><th>Segment</th><th>Length</th><th>Thickness</th></tr>"
        table_rows = "".join(
            f"<tr><td>{escape(r[0])}</td><td>{escape(r[2])}</td><td>{escape(r[4])}</td></tr>"
            for r in rows
        )

    notes = "".join(f"<li>{escape(n)}</li>" for n in change_notes)
    notes_block = f'<h2>Notes</h2><ul class="notes">{notes}</ul>' if notes else ""

    return f"""\
<style>
  body {{
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    color: #1b1d23; margin: 0; padding: 24px;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{
    font-size: 13px; text-transform: uppercase;
    letter-spacing: 0.05em; color: #6b7280; margin: 20px 0 8px;
  }}
  .subject {{ color: #6b7280; font-size: 13px; margin: 0 0 16px; }}
  .summary {{ font-size: 14px; margin: 0 0 20px; max-width: 60ch; }}
  .diagrams {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .diagram {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; width: 260px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px; }}
  th, td {{
    text-align: left; padding: 8px 10px;
    border-bottom: 1px solid #e5e7eb; vertical-align: middle;
  }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #6b7280; }}
  ul.notes {{ font-size: 13px; color: #4b5563; margin: 0; padding-left: 20px; }}
</style>
<h1>{escape(name)}</h1>
<p class="subject">{escape(subject_name)}</p>
<p class="summary">{escape(summary)}</p>
<div class="diagrams">{diagrams}</div>
<h2>Dimensions</h2>
<table>
  <thead>{header}</thead>
  <tbody>{table_rows}</tbody>
</table>
{notes_block}
"""


class AuthorDesignSketchHandler(SkillBase[AuthorDesignSketchInput, AuthorDesignSketchOutput]):
    """Deterministically renders a scaled 2D kinematic-chain diagram (the
    actual sketch -- segments drawn end-to-end, proportioned and angled
    from real mm values) plus a supporting dimension table, and persists
    it as a DESIGN_SKETCH work product via twin.commit_design_sketch.

    Fills the gap between decide_sketch_needed (decides IF a sketch is
    needed) and the raw commit tool (which only stores whatever HTML a
    caller hands it, with no structure enforced) -- and, unlike this
    skill's first version, actually draws the part instead of only
    tabulating before/after numbers. A table of "50mm -> 60mm" tells a
    reviewer a number changed; it doesn't show them what the part looks
    like, which is the entire point of a sketch.
    """

    input_type = AuthorDesignSketchInput
    output_type = AuthorDesignSketchOutput

    async def validate_preconditions(self, input_data: AuthorDesignSketchInput) -> list[str]:
        errors: list[str] = []
        if not await self.context.mcp.is_available("twin.commit_design_sketch"):
            errors.append("twin.commit_design_sketch tool is not available")
        return errors

    async def execute(self, input_data: AuthorDesignSketchInput) -> AuthorDesignSketchOutput:
        is_revision = bool(input_data.before_segments)
        self.logger.info(
            "Authoring design sketch",
            subject_name=input_data.subject_name,
            segment_count=len(input_data.after_segments),
            is_revision=is_revision,
        )
        html_content = _render_html(
            input_data.name,
            input_data.subject_name,
            input_data.summary,
            input_data.before_segments,
            input_data.after_segments,
            input_data.change_notes,
        )
        result = await self.context.mcp.invoke(
            "twin.commit_design_sketch",
            {
                "name": input_data.name,
                "html_content": html_content,
                "description_text": input_data.summary,
                "source_node_ids": input_data.source_node_ids or None,
                "project_id": input_data.project_id,
                "domain": input_data.domain,
                "source_tool": "mechanical.author_design_sketch",
            },
        )
        return AuthorDesignSketchOutput(
            node_id=result["node_id"],
            segment_count=len(input_data.after_segments),
            is_revision=is_revision,
            approved=False,
        )
