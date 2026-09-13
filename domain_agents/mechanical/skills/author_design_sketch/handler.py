"""Handler for the author_design_sketch skill."""

from __future__ import annotations

import re
from html import escape

from skill_registry.skill_base import SkillBase

from .schema import AuthorDesignSketchInput, AuthorDesignSketchOutput, ProposedChange

_LEADING_NUMBER = re.compile(r"[-+]?\d*\.?\d+")


def _leading_number(value: str) -> float | None:
    match = _LEADING_NUMBER.match(value.strip())
    return float(match.group()) if match else None


def _bar_row(before: str, after: str) -> str:
    """A dependency-free before/after bar comparison -- CSS width percentages
    against the larger of the two magnitudes. Empty string when either side
    isn't a parseable magnitude (a change like 'add a guard' has no bar)."""
    b, a = _leading_number(before), _leading_number(after)
    if b is None or a is None or (b <= 0 and a <= 0):
        return ""
    scale = max(abs(b), abs(a)) or 1.0
    b_pct, a_pct = max(2.0, abs(b) / scale * 100), max(2.0, abs(a) / scale * 100)
    return (
        '<div class="bars">'
        f'<div class="bar before" style="width:{b_pct:.0f}%"></div>'
        f'<div class="bar after" style="width:{a_pct:.0f}%"></div>'
        "</div>"
    )


def _render_html(name: str, subject_name: str, summary: str, changes: list[ProposedChange]) -> str:
    rows = []
    for c in changes:
        rows.append(
            "<tr>"
            f"<td>{escape(c.feature)}</td>"
            f'<td class="before">{escape(c.before)}</td>'
            f'<td class="after">{escape(c.after)}</td>'
            f"<td>{_bar_row(c.before, c.after)}</td>"
            f'<td class="rationale">{escape(c.rationale)}</td>'
            "</tr>"
        )
    return f"""\
<style>
  body {{
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    color: #1b1d23; margin: 0; padding: 24px;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subject {{ color: #6b7280; font-size: 13px; margin: 0 0 16px; }}
  .summary {{ font-size: 14px; margin: 0 0 20px; max-width: 60ch; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{
    text-align: left; padding: 8px 10px;
    border-bottom: 1px solid #e5e7eb; vertical-align: middle;
  }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: #6b7280; }}
  td.before {{ color: #6b7280; }}
  td.after {{ color: #1d4ed8; font-weight: 600; }}
  td.rationale {{ color: #4b5563; max-width: 28ch; }}
  .bars {{ display: flex; flex-direction: column; gap: 3px; width: 120px; }}
  .bar {{ height: 8px; border-radius: 3px; }}
  .bar.before {{ background: #d1d5db; }}
  .bar.after {{ background: #3b82f6; }}
</style>
<h1>{escape(name)}</h1>
<p class="subject">{escape(subject_name)}</p>
<p class="summary">{escape(summary)}</p>
<table>
  <thead><tr><th>Feature</th><th>Before</th><th>After</th><th>Comparison</th><th>Rationale</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
"""


class AuthorDesignSketchHandler(SkillBase[AuthorDesignSketchInput, AuthorDesignSketchOutput]):
    """Deterministically renders a styled before/after comparison sketch and
    persists it as a DESIGN_SKETCH work product via twin.commit_design_sketch.

    Fills the gap between decide_sketch_needed (decides IF a sketch is
    needed) and the raw commit tool (which only stores whatever HTML a
    caller hands it, with no structure enforced) -- without this skill, a
    chat agent free-writes arbitrary unstyled prose HTML per call, which is
    exactly what a design sketch should NOT be: a consistent, reviewable
    proportions/topology reference, not a paragraph.
    """

    input_type = AuthorDesignSketchInput
    output_type = AuthorDesignSketchOutput

    async def validate_preconditions(self, input_data: AuthorDesignSketchInput) -> list[str]:
        errors: list[str] = []
        if not await self.context.mcp.is_available("twin.commit_design_sketch"):
            errors.append("twin.commit_design_sketch tool is not available")
        return errors

    async def execute(self, input_data: AuthorDesignSketchInput) -> AuthorDesignSketchOutput:
        self.logger.info(
            "Authoring design sketch",
            subject_name=input_data.subject_name,
            change_count=len(input_data.proposed_changes),
        )
        html_content = _render_html(
            input_data.name,
            input_data.subject_name,
            input_data.summary,
            input_data.proposed_changes,
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
            change_count=len(input_data.proposed_changes),
            approved=False,
        )
