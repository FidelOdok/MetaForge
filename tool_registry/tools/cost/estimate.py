"""A rough manufacturing cost estimate for early ranking (FORGE-615).

No CNC or sheet-metal service offers a buyer API (FORGE-614), so before a
real quote exists the route phase has nothing to put in a candidate's cost.
This fills that gap with arithmetic, and is explicit that it is only that:

* Every input is a figure the caller supplies **with its source** (a shop's
  published rate, a material supplier's price, a measured volume). Nothing
  is built in and nothing is defaulted. A missing figure makes the parts of
  the estimate that need it ``unknown``.
* The result is always ``kind: "estimate"`` with ``not_a_quote``, the list
  of what it leaves out, and every input's source, so it cannot be mistaken
  for a price.

The model, per unit:

``cnc_milling``
    material = stock volume x density x price per kg;
    machining time = (stock volume - part volume) / material removal rate.
``sheet_metal``
    material = blank area x thickness x density x price per kg;
    process time = cut length / cut speed + bends x seconds per bend.

Process cost is time x shop rate; setup (hours x shop rate) is per batch and
spread over the quantity. Removed volume is a coarse proxy for machining
effort: feature count, tolerance and fixturing are not in it, and the
result says so.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Process = Literal["cnc_milling", "sheet_metal"]

#: What every estimate leaves out, said on every result.
EXCLUDES = (
    "finishing and post-processing",
    "tolerance, feature count and fixturing (machining time is from removed volume only)",
    "tool wear and consumables",
    "shipping",
    "the shop's margin and overheads beyond its hourly rate",
)


class Figure(BaseModel):
    """A number and where it came from."""

    value: float = Field(..., ge=0)
    source: str = Field(..., min_length=1, description="Where the figure came from")


class Dims(BaseModel):
    value: list[float] = Field(..., min_length=3, max_length=3)
    source: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _positive(self) -> Dims:
        if any(v <= 0 for v in self.value):
            raise ValueError("dimensions must be positive")
        return self


class EstimateRequest(BaseModel):
    process: Process
    quantity: int = Field(..., ge=1)
    currency: str = Field(..., min_length=3, max_length=3, description="ISO code, e.g. GBP")
    density_kg_per_m3: Figure | None = None
    material_price_per_kg: Figure | None = None
    shop_rate_per_hour: Figure | None = None
    setup_hours: Figure | None = None
    # cnc_milling
    stock_dims_mm: Dims | None = None
    part_volume_mm3: Figure | None = None
    removal_rate_mm3_per_min: Figure | None = None
    # sheet_metal
    thickness_mm: Figure | None = None
    blank_area_mm2: Figure | None = None
    cut_length_mm: Figure | None = None
    cut_speed_mm_per_min: Figure | None = None
    bend_count: int | None = Field(default=None, ge=0)
    seconds_per_bend: Figure | None = None

    @model_validator(mode="after")
    def _fields_for_process(self) -> EstimateRequest:
        cnc = ("stock_dims_mm", "part_volume_mm3", "removal_rate_mm3_per_min")
        sheet = (
            "thickness_mm",
            "blank_area_mm2",
            "cut_length_mm",
            "cut_speed_mm_per_min",
            "bend_count",
            "seconds_per_bend",
        )
        wrong = sheet if self.process == "cnc_milling" else cnc
        given = [f for f in wrong if getattr(self, f) is not None]
        if given:
            raise ValueError(f"{', '.join(given)} do not apply to {self.process}")
        return self


def _v(fig: Figure | None) -> float | None:
    return fig.value if fig is not None else None


def estimate(request: EstimateRequest) -> dict[str, Any]:
    r = request
    unknown: list[str] = []

    def need(name: str, value: float | None) -> float | None:
        if value is None:
            unknown.append(name)
        return value

    density = need("density_kg_per_m3", _v(r.density_kg_per_m3))
    price = need("material_price_per_kg", _v(r.material_price_per_kg))
    rate = need("shop_rate_per_hour", _v(r.shop_rate_per_hour))
    setup_h = need("setup_hours", _v(r.setup_hours))

    material_m3: float | None = None
    minutes: float | None = None
    basis: dict[str, str] = {}
    if r.process == "cnc_milling":
        stock = r.stock_dims_mm
        if stock is None:
            unknown.append("stock_dims_mm")
        stock_mm3 = stock.value[0] * stock.value[1] * stock.value[2] if stock else None
        part = need("part_volume_mm3", _v(r.part_volume_mm3))
        mrr = need("removal_rate_mm3_per_min", _v(r.removal_rate_mm3_per_min))
        if stock_mm3 is not None and part is not None and part > stock_mm3:
            raise ValueError("part_volume_mm3 is larger than the stock; check both")
        material_m3 = stock_mm3 * 1e-9 if stock_mm3 is not None else None
        if stock_mm3 is not None and part is not None and mrr:
            minutes = (stock_mm3 - part) / mrr
        basis["material"] = "stock volume x density x price per kg"
        basis["process"] = "(stock volume - part volume) / removal rate x shop rate"
    else:
        thickness = need("thickness_mm", _v(r.thickness_mm))
        area = need("blank_area_mm2", _v(r.blank_area_mm2))
        cut = need("cut_length_mm", _v(r.cut_length_mm))
        speed = need("cut_speed_mm_per_min", _v(r.cut_speed_mm_per_min))
        if r.bend_count is None:
            unknown.append("bend_count")
        per_bend = _v(r.seconds_per_bend) if r.bend_count else 0.0
        if r.bend_count and per_bend is None:
            unknown.append("seconds_per_bend")
        if thickness is not None and area is not None:
            material_m3 = thickness * area * 1e-9
        if cut is not None and speed and r.bend_count is not None and per_bend is not None:
            minutes = cut / speed + r.bend_count * per_bend / 60.0
        basis["material"] = "blank area x thickness x density x price per kg"
        basis["process"] = "(cut length / cut speed + bends x seconds per bend) x shop rate"

    material = (
        material_m3 * density * price
        if material_m3 is not None and density is not None and price is not None
        else None
    )
    process = minutes / 60.0 * rate if minutes is not None and rate is not None else None
    setup_batch = setup_h * rate if setup_h is not None and rate is not None else None
    unit_variable = material + process if material is not None and process is not None else None
    unit = (
        unit_variable + setup_batch / r.quantity
        if unit_variable is not None and setup_batch is not None
        else None
    )

    def money(x: float | None) -> float | None:
        return round(x, 2) if x is not None else None

    sources = {
        name: fig.source
        for name in (
            "density_kg_per_m3",
            "material_price_per_kg",
            "shop_rate_per_hour",
            "setup_hours",
            "stock_dims_mm",
            "part_volume_mm3",
            "removal_rate_mm3_per_min",
            "thickness_mm",
            "blank_area_mm2",
            "cut_length_mm",
            "cut_speed_mm_per_min",
            "seconds_per_bend",
        )
        if (fig := getattr(r, name)) is not None
    }
    result: dict[str, Any] = {
        "kind": "estimate",
        "not_a_quote": (
            "A rough estimate from the figures given, for comparing routes early. It is not a "
            "price: get a quote before deciding on cost."
        ),
        "process": r.process,
        "quantity": r.quantity,
        "currency": r.currency.upper(),
        "per_unit": {
            "material": money(material),
            "process_minutes": round(minutes, 2) if minutes is not None else None,
            "process": money(process),
            "setup_share": money(setup_batch / r.quantity if setup_batch is not None else None),
            "total": money(unit),
        },
        "setup_per_batch": money(setup_batch),
        "batch_total": money(unit * r.quantity if unit is not None else None),
        "basis": basis,
        "unknown_inputs": sorted(set(unknown)),
        "sources": sources,
        "excludes": list(EXCLUDES),
    }
    if unit_variable is not None and setup_batch is not None:
        result["for_ranking"] = {
            "setup_cost": money(setup_batch),
            "unit_cost": money(unit_variable),
            "source_refs": [
                f"estimate: cost.estimate_manufacturing ({r.process}, not a quote)",
                *sorted(set(sources.values())),
            ],
        }
    return result
