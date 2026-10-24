"""Static worst-case power budget per supply rail (FORGE-544).

Pure arithmetic over figures the caller supplies: each rail's source and
rated output, each load's worst-case draw, and the derating rule. Nothing is
looked up and nothing is assumed. A load or capacity that is not given is
*unknown*, and a rail whose total depends on an unknown cannot pass; it is
``not_established`` (unless the figures it does have already exceed the
allowance, which is a fail whatever the unknowns turn out to be).

Regulator input current is carried upstream: a linear regulator draws its
output current plus its quiescent current from its input rail; a switching
regulator draws ``Vout * Iout / (efficiency * Vin)``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SourceKind = Literal["supply", "ldo", "switching"]
RailStatus = Literal["pass", "fail", "not_established"]


class Rail(BaseModel):
    """One supply rail and what feeds it."""

    name: str = Field(..., min_length=1)
    voltage_v: float = Field(..., gt=0)
    source_kind: SourceKind = Field(
        ...,
        description=(
            "supply: a battery or external input (a root); ldo: a linear "
            "regulator; switching: a buck/boost converter"
        ),
    )
    rated_current_ma: float | None = Field(
        default=None, ge=0, description="Rated output current of the source; None is unknown"
    )
    input_rail: str | None = Field(
        default=None, description="Rail feeding this regulator (ldo/switching only)"
    )
    efficiency: float | None = Field(
        default=None, gt=0, le=1, description="Converter efficiency at this load (switching)"
    )
    quiescent_ma: float | None = Field(
        default=None, ge=0, description="Regulator quiescent current (ldo)"
    )
    source: str = Field(default="", description="Where the rating came from (datasheet, page)")

    @model_validator(mode="after")
    def _regulator_fields(self) -> Rail:
        if self.source_kind == "supply" and self.input_rail:
            raise ValueError(f"rail {self.name!r}: a supply has no input_rail")
        if self.source_kind != "supply" and not self.input_rail:
            raise ValueError(f"rail {self.name!r}: a {self.source_kind} needs input_rail")
        return self


class Load(BaseModel):
    """One consumer on one rail."""

    name: str = Field(..., min_length=1)
    rail: str = Field(..., min_length=1)
    current_ma: float | None = Field(default=None, ge=0)
    power_mw: float | None = Field(default=None, ge=0)
    source: str = Field(default="", description="Where the figure came from")

    @model_validator(mode="after")
    def _one_figure(self) -> Load:
        if self.current_ma is not None and self.power_mw is not None:
            raise ValueError(f"load {self.name!r}: give current_ma or power_mw, not both")
        return self


class BudgetRequest(BaseModel):
    rails: list[Rail] = Field(..., min_length=1)
    loads: list[Load] = Field(default_factory=list)
    derating: float = Field(
        ...,
        gt=0,
        le=1,
        description="Allowed share of each rated output, e.g. 0.8 for 'load <= 80 %'",
    )


class RailBudget(BaseModel):
    name: str
    voltage_v: float
    source_kind: SourceKind
    own_load_ma: float = Field(description="Known draw of the loads directly on this rail")
    downstream_ma: float = Field(description="Known input current of regulators fed from it")
    load_ma: float = Field(description="own_load_ma + downstream_ma (known part)")
    rated_current_ma: float | None
    allowed_ma: float | None
    headroom_ma: float | None
    headroom_pct: float | None = Field(description="headroom as a share of allowed, in %")
    status: RailStatus
    unknowns: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BudgetResult(BaseModel):
    verdict: RailStatus
    passed: bool
    derating: float
    rails: list[RailBudget]
    worst_rail: str | None
    source_power_mw: float | None = Field(
        description="Power drawn from the supply rails; None when any of it is unknown"
    )


def check_budget(request: BudgetRequest) -> BudgetResult:
    """Compute the per-rail budget. Raises ``ValueError`` on an ill-formed tree."""
    rails = {r.name: r for r in request.rails}
    if len(rails) != len(request.rails):
        raise ValueError("rail names must be unique")
    for rail in request.rails:
        if rail.input_rail is not None and rail.input_rail not in rails:
            raise ValueError(f"rail {rail.name!r}: input_rail {rail.input_rail!r} is not a rail")
    for load in request.loads:
        if load.rail not in rails:
            raise ValueError(f"load {load.name!r}: rail {load.rail!r} is not a rail")

    order = _leaves_first(rails)
    children: dict[str, list[str]] = {name: [] for name in rails}
    for rail in request.rails:
        if rail.input_rail is not None:
            children[rail.input_rail].append(rail.name)

    known_out: dict[str, float] = {}
    unknowns: dict[str, list[str]] = {name: [] for name in rails}
    notes: dict[str, list[str]] = {name: [] for name in rails}
    own: dict[str, float] = {}
    down: dict[str, float] = {}

    for name in order:
        rail = rails[name]
        own_ma = 0.0
        for load in request.loads:
            if load.rail != name:
                continue
            if load.current_ma is not None:
                own_ma += load.current_ma
            elif load.power_mw is not None:
                own_ma += load.power_mw / rail.voltage_v
            else:
                unknowns[name].append(f"load {load.name}: no current or power given")
        down_ma = 0.0
        for child_name in children[name]:
            child = rails[child_name]
            child_in, child_unknown = _input_current(child, known_out[child_name], rail)
            down_ma += child_in
            if unknowns[child_name]:
                unknowns[name].append(f"rail {child_name} has unknown load")
            if child_unknown:
                unknowns[name].append(child_unknown)
            if child.source_kind == "ldo" and child.quiescent_ma is None:
                notes[name].append(f"{child_name}: LDO quiescent current not given, counted as 0")
        own[name] = own_ma
        down[name] = down_ma
        known_out[name] = own_ma + down_ma

    budgets = [
        _rail_budget(rails[n], own[n], down[n], request.derating, unknowns[n], notes[n])
        for n in rails
    ]
    verdict: RailStatus
    if any(b.status == "fail" for b in budgets):
        verdict = "fail"
    elif any(b.status == "not_established" for b in budgets):
        verdict = "not_established"
    else:
        verdict = "pass"

    source_power: float | None = 0.0
    for b in budgets:
        if b.source_kind != "supply":
            continue
        if b.unknowns or source_power is None:
            source_power = None
            continue
        source_power += b.load_ma * b.voltage_v

    return BudgetResult(
        verdict=verdict,
        passed=verdict == "pass",
        derating=request.derating,
        rails=budgets,
        worst_rail=_worst(budgets),
        source_power_mw=round(source_power, 3) if source_power is not None else None,
    )


def _input_current(child: Rail, out_ma: float, parent: Rail) -> tuple[float, str]:
    """Current a regulator draws from its input rail for ``out_ma`` out."""
    if child.source_kind == "ldo":
        return out_ma + (child.quiescent_ma or 0.0), ""
    if child.efficiency is None:
        return 0.0, f"rail {child.name}: switching regulator efficiency not given"
    return child.voltage_v * out_ma / (child.efficiency * parent.voltage_v), ""


def _rail_budget(
    rail: Rail,
    own_ma: float,
    down_ma: float,
    derating: float,
    unknowns: list[str],
    notes: list[str],
) -> RailBudget:
    load = own_ma + down_ma
    rail_unknowns = list(unknowns)
    allowed = headroom = headroom_pct = None
    if rail.rated_current_ma is None:
        rail_unknowns.append("rated output current not given")
    else:
        allowed = rail.rated_current_ma * derating
        headroom = allowed - load
        headroom_pct = (headroom / allowed * 100.0) if allowed > 0 else None
    status: RailStatus
    if allowed is not None and load > allowed:
        status = "fail"  # the known part alone is over, whatever the unknowns are
    elif rail_unknowns:
        status = "not_established"
    else:
        status = "pass"
    return RailBudget(
        name=rail.name,
        voltage_v=rail.voltage_v,
        source_kind=rail.source_kind,
        own_load_ma=round(own_ma, 3),
        downstream_ma=round(down_ma, 3),
        load_ma=round(load, 3),
        rated_current_ma=rail.rated_current_ma,
        allowed_ma=round(allowed, 3) if allowed is not None else None,
        headroom_ma=round(headroom, 3) if headroom is not None else None,
        headroom_pct=round(headroom_pct, 2) if headroom_pct is not None else None,
        status=status,
        unknowns=rail_unknowns,
        notes=notes,
    )


def _worst(budgets: list[RailBudget]) -> str | None:
    """The failing rail furthest over, else the established rail with least headroom."""
    failing = [b for b in budgets if b.status == "fail" and b.headroom_ma is not None]
    if failing:
        return min(failing, key=lambda b: b.headroom_pct if b.headroom_pct is not None else 0).name
    ranked = [b for b in budgets if b.headroom_pct is not None and not b.unknowns]
    if ranked:
        return min(ranked, key=lambda b: b.headroom_pct or 0.0).name
    return None


def _leaves_first(rails: dict[str, Rail]) -> list[str]:
    """Rails ordered so every regulator comes before the rail feeding it."""
    depth: dict[str, int] = {}

    def walk(name: str, seen: tuple[str, ...]) -> int:
        if name in seen:
            raise ValueError(f"rails form a loop: {' -> '.join((*seen, name))}")
        if name not in depth:
            parent = rails[name].input_rail
            depth[name] = 0 if parent is None else walk(parent, (*seen, name)) + 1
        return depth[name]

    for name in rails:
        walk(name, ())
    return sorted(rails, key=lambda n: -depth[n])


def check_budget_dict(arguments: dict[str, Any]) -> dict[str, Any]:
    """Wire form: validate ``arguments`` and return the result as JSON data."""
    return check_budget(BudgetRequest.model_validate(arguments)).model_dump(mode="json")
