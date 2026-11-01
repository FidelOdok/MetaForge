"""Handler for the form_factor_envelope skill (FORGE-633).

Pure arithmetic over axis-aligned boxes. Positioned components are unioned;
otherwise they are stacked along ``stack_axis`` with ``gap_mm`` between
them, the other two axes taking the largest component. The inner envelope
adds the clearance on every side, the outer adds the wall. Nothing is
assumed: clearance and wall come with their sources, and the fit check
runs only against a stated, sourced maximum.
"""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import Component, FormFactorEnvelopeInput, FormFactorEnvelopeOutput

_AXES = {"x": 0, "y": 1, "z": 2}


def _fmt(values: list[float]) -> str:
    return " x ".join(f"{v:g}" for v in values)


def _overlap(a: Component, b: Component) -> bool:
    assert a.position_mm is not None and b.position_mm is not None
    return all(
        a.position_mm[i] < b.position_mm[i] + b.dims_mm[i]
        and b.position_mm[i] < a.position_mm[i] + a.dims_mm[i]
        for i in range(3)
    )


class FormFactorEnvelopeHandler(SkillBase[FormFactorEnvelopeInput, FormFactorEnvelopeOutput]):
    """The outer envelope a set of components needs. No MCP tools."""

    input_type = FormFactorEnvelopeInput
    output_type = FormFactorEnvelopeOutput

    async def execute(self, input_data: FormFactorEnvelopeInput) -> FormFactorEnvelopeOutput:
        comps = input_data.components
        positioned = [c.position_mm is not None for c in comps]
        if any(positioned) and not all(positioned):
            raise ValueError("give position_mm for every component or for none")
        if any(d <= 0 for c in comps for d in c.dims_mm):
            raise ValueError("every component dimension must be positive")
        self.logger.info("Computing form factor envelope", components=len(comps))

        overlaps: list[str] = []
        if all(positioned):
            layout = "positioned"
            lo = [min(c.position_mm[i] for c in comps if c.position_mm) for i in range(3)]
            hi = [
                max(c.position_mm[i] + c.dims_mm[i] for c in comps if c.position_mm)
                for i in range(3)
            ]
            contents = [hi[i] - lo[i] for i in range(3)]
            overlaps = [
                f"{a.name} and {b.name} overlap"
                for n, a in enumerate(comps)
                for b in comps[n + 1 :]
                if _overlap(a, b)
            ]
        else:
            if input_data.stack_axis is None:
                raise ValueError("unpositioned components need a stack_axis (x, y or z)")
            layout = "stacked"
            axis = _AXES[input_data.stack_axis]
            contents = [max(c.dims_mm[i] for c in comps) for i in range(3)]
            contents[axis] = sum(c.dims_mm[axis] for c in comps) + input_data.gap_mm * (
                len(comps) - 1
            )

        clearance = input_data.clearance.value_mm
        wall = input_data.wall_thickness.value_mm
        inner = [d + 2 * clearance for d in contents]
        outer = [d + 2 * wall for d in inner]
        inner_volume = inner[0] * inner[1] * inner[2] / 1000.0
        outer_volume = outer[0] * outer[1] * outer[2] / 1000.0
        component_volume = sum(c.dims_mm[0] * c.dims_mm[1] * c.dims_mm[2] for c in comps) / 1000.0

        messages = [
            f"contents {_fmt(contents)} mm ({layout}); inner {_fmt(inner)} mm with "
            f"{clearance:g} mm clearance; outer {_fmt(outer)} mm with a {wall:g} mm wall"
        ]
        messages += overlaps
        sources = [
            f"clearance: {input_data.clearance.source_ref}",
            f"wall thickness: {input_data.wall_thickness.source_ref}",
        ]
        fits: bool | None = None
        margin: list[float] | None = None
        if input_data.max_envelope is not None:
            limit = input_data.max_envelope
            margin = [
                round(m - o, 3) for m, o in zip(sorted(limit.dims_mm), sorted(outer), strict=True)
            ]
            fits = all(m >= 0 for m in margin)
            sources.append(f"max envelope: {limit.source_ref}")
            messages.append(
                f"outer {_fmt(outer)} mm {'fits within' if fits else 'exceeds'} the maximum "
                f"{_fmt(limit.dims_mm)} mm (any orientation; margins {_fmt(margin)} mm)"
            )
        else:
            messages.append("no maximum envelope given, so the fit was not checked")

        return FormFactorEnvelopeOutput(
            layout=layout,
            contents_dims_mm=[round(v, 3) for v in contents],
            inner_dims_mm=[round(v, 3) for v in inner],
            outer_dims_mm=[round(v, 3) for v in outer],
            outer_volume_cm3=round(outer_volume, 3),
            inner_volume_cm3=round(inner_volume, 3),
            component_volume_cm3=round(component_volume, 3),
            fill_ratio=round(component_volume / inner_volume, 4) if inner_volume else 0.0,
            fits=fits,
            margin_mm=margin,
            overlaps=overlaps,
            basis=[f"{c.name}: {c.basis}" for c in comps],
            sources=sources,
            messages=messages,
        )
