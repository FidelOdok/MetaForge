"""Evaluate an interface contract against the items' current revisions (pure).

Each check answers with one of four words and never guesses:

* ``satisfied``: both sides' data exists and agrees within the tolerance;
* ``violated``: it exists and disagrees, and ``values`` says by how much;
* ``not_evaluated``: an input is missing, and ``missing`` names it with the
  step that would produce it (no default stands in for a missing number);
* ``stale``: an input was made from a revision that has since moved.

The checks read the same metadata the rest of the twin reads, so nothing is
recorded twice: a board's ``dimensions``, ``mounting_holes``, ``connectors``
and ``component_max_height`` (as ``kicad`` recorders and the cross-domain
constraint checks read them); an enclosure's ``internal_envelope`` (or its
``dimensions`` less ``internal_clearance``), ``mounting_standoffs`` and
``cutouts``; a pin map's rows (pin, signal, net) and the firmware's use of
them from the ``pin_maps_to`` edges the interface proposals infer. Positions
are compared in the frame both sides record them in (the base's assembly
frame), in millimetres.

The gateway builds :class:`CheckInputs` from the twin and stores the result;
nothing here does I/O.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from twin_core.consistency.validation_status import ValidationStatus
from twin_core.models.interface_contract import (
    CheckOutcome,
    ContractAgreement,
    ContractCheck,
    ContractEvaluation,
    ContractKind,
    ContractStatus,
    CoupledParameter,
    ExpectedArtifact,
    InterfaceContract,
)

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class ItemSnapshot(BaseModel):
    """One linked item's current revision, as the checks read it."""

    key: str
    revision: int
    item_type: str = ""
    name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.revision}"


class ArtifactSnapshot(BaseModel):
    """One manufacturing artifact found in the project, with what it was made from."""

    ref: str
    item_type: str = ""
    component_id: str | None = None
    files: list[str] = Field(default_factory=list)
    #: ``{"pinned": "PCB-MAIN@1", "current": "PCB-MAIN@2"}`` per source it records.
    pins: list[dict[str, str]] = Field(default_factory=list)
    #: The record's own status when it is a check result (``PASS``, ``FAIL``).
    status: str | None = None


class CheckInputs(BaseModel):
    """Everything a contract's checks may read."""

    #: Each linked role's item at its current head; absent when not linked yet.
    items: dict[str, ItemSnapshot] = Field(default_factory=dict)
    #: The linked pin map's rows (``pin``, ``signal``, ``net``, ``peripheral``).
    pinmap: list[dict[str, str]] = Field(default_factory=list)
    #: Pins, peripherals and nets the firmware names, from ``pin_maps_to`` edges.
    firmware_pins: list[str] = Field(default_factory=list)
    #: True when a ``pin_maps_to`` edge joins the linked pin map and firmware.
    pin_edge: bool = False
    #: The manufacturing context's fields, ``{name: {"value", "authority"}}``.
    context: dict[str, dict[str, Any]] = Field(default_factory=dict)
    context_ref: str | None = None
    #: Manufacturing artifacts found, by artifact kind.
    artifacts: dict[str, list[ArtifactSnapshot]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _wh(raw: Any) -> tuple[float, float] | None:
    """``(width, height)`` from ``{width, height}``, ``{width, depth}`` or ``[w, h]``."""
    if isinstance(raw, dict):
        w = _num(raw.get("width"))
        # A box lies in width x depth (its height is vertical); a board
        # outline is width x height.
        h = _num(raw.get("depth") if raw.get("depth") is not None else raw.get("height"))
        return (w, h) if w is not None and h is not None else None
    if isinstance(raw, list | tuple) and len(raw) >= 2:
        w, h = _num(raw[0]), _num(raw[1])
        return (w, h) if w is not None and h is not None else None
    return None


def _xy(raw: Any) -> tuple[float, float] | None:
    if isinstance(raw, dict):
        x, y = _num(raw.get("x")), _num(raw.get("y"))
        return (x, y) if x is not None and y is not None else None
    if isinstance(raw, list | tuple) and len(raw) >= 2:
        x, y = _num(raw[0]), _num(raw[1])
        return (x, y) if x is not None and y is not None else None
    return None


def _xyz(raw: Any) -> tuple[float, ...] | None:
    if isinstance(raw, dict):
        vals = [_num(raw.get(k)) for k in ("x", "y", "z") if raw.get(k) is not None]
    elif isinstance(raw, list | tuple):
        vals = [_num(v) for v in raw]
    else:
        return None
    if len(vals) < 2 or any(v is None for v in vals):
        return None
    return tuple(float(v) for v in vals if v is not None)


def _dist(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    n = min(len(a), len(b))
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)))


def _r(value: float) -> float:
    return round(value, 3)


def _norm(text: Any) -> str:
    return "".join(ch for ch in str(text or "").upper() if ch.isalnum())


class _Builder:
    """Collects the checks of one evaluation."""

    def __init__(self, contract: InterfaceContract, inputs: CheckInputs) -> None:
        self.contract = contract
        self.inputs = inputs
        self.checks: list[ContractCheck] = []

    def item(self, role: str) -> ItemSnapshot | None:
        return self.inputs.items.get(role)

    def missing_item(self, role: str) -> str:
        link = self.contract.link(role)
        label = link.label if link else role
        how = f" (produced by {link.produced_by})" if link and link.produced_by else ""
        return f"{label}: not linked yet{how}"

    def missing_param(self, param: CoupledParameter | None, name: str) -> str:
        label = param.label if param else name
        owner = f", owned by {param.owner}" if param else ""
        return f"agreed value for {label}: unknown{owner}"

    def add(
        self,
        id: str,  # noqa: A002 - the check's own field name
        label: str,
        plain: str,
        outcome: CheckOutcome,
        message: str,
        *,
        values: dict[str, Any] | None = None,
        missing: list[str] | None = None,
        roles: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
        extra_inputs: list[str] | None = None,
    ) -> None:
        inputs = [snap.ref for r in roles if (snap := self.item(r)) is not None]
        self.checks.append(
            ContractCheck(
                id=id,
                label=label,
                plain=plain,
                outcome=outcome,
                message=message,
                values=values or {},
                missing=missing or [],
                inputs=inputs + list(extra_inputs or []),
                parameters=list(parameters),
            )
        )

    def not_evaluated(
        self,
        id: str,  # noqa: A002
        label: str,
        plain: str,
        missing: list[str],
        *,
        roles: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
    ) -> None:
        self.add(
            id,
            label,
            plain,
            CheckOutcome.NOT_EVALUATED,
            "Not checked: " + "; ".join(missing),
            missing=missing,
            roles=roles,
            parameters=parameters,
        )


# ---------------------------------------------------------------------------
# CAD to EDA
# ---------------------------------------------------------------------------


def _envelope(meta: dict[str, Any]) -> tuple[tuple[float, float] | None, str]:
    inner = _wh(meta.get("internal_envelope"))
    if inner is not None:
        return inner, "internal_envelope"
    outer = _wh(meta.get("dimensions"))
    clearance = _num(meta.get("internal_clearance"))
    if outer is not None and clearance is not None:
        return (outer[0] - 2 * clearance, outer[1] - 2 * clearance), (
            "dimensions less internal_clearance"
        )
    return None, ""


def _board_outline(meta: dict[str, Any]) -> tuple[float, float] | None:
    return _wh(meta.get("dimensions")) or _wh(meta.get("board_outline"))


def _cad_eda(b: _Builder) -> None:
    c = b.contract
    pcb, enc = b.item("pcb"), b.item("enclosure")
    pcb_meta = pcb.metadata if pcb else {}
    enc_meta = enc.metadata if enc else {}

    # 1. The board fits the base's internal envelope with the agreed clearance.
    plain = "The circuit board must fit inside the base"
    label = "PCB outline fits the enclosure's internal envelope with clearance"
    clearance_p = c.parameter("pcb_clearance")
    missing: list[str] = []
    if pcb is None:
        missing.append(b.missing_item("pcb"))
    if enc is None:
        missing.append(b.missing_item("enclosure"))
    board = _board_outline(pcb_meta) if pcb else None
    envelope, envelope_source = _envelope(enc_meta) if enc else (None, "")
    if pcb is not None and board is None:
        missing.append(f"{pcb.ref}: no board outline recorded (dimensions width and height)")
    if enc is not None and envelope is None:
        missing.append(f"{enc.ref}: no internal envelope recorded")
    clearance = _num(clearance_p.value) if clearance_p and clearance_p.known else None
    if clearance is None:
        missing.append(b.missing_param(clearance_p, "pcb_clearance"))
    if missing or board is None or envelope is None or clearance is None:
        b.not_evaluated(
            "pcb_fits_envelope",
            label,
            plain,
            missing,
            roles=("pcb", "enclosure"),
            parameters=("pcb_outline", "pcb_clearance"),
        )
    else:
        margin_w = envelope[0] - board[0] - 2 * clearance
        margin_h = envelope[1] - board[1] - 2 * clearance
        ok = margin_w >= 0 and margin_h >= 0
        b.add(
            "pcb_fits_envelope",
            label,
            plain,
            CheckOutcome.SATISFIED if ok else CheckOutcome.VIOLATED,
            (
                f"The {board[0]:g} x {board[1]:g} mm board fits the {envelope[0]:g} x "
                f"{envelope[1]:g} mm envelope with {clearance:g} mm clearance"
                if ok
                else f"The {board[0]:g} x {board[1]:g} mm board needs "
                f"{board[0] + 2 * clearance:g} x {board[1] + 2 * clearance:g} mm; the envelope "
                f"is {envelope[0]:g} x {envelope[1]:g} mm"
            ),
            values={
                "board_mm": list(board),
                "envelope_mm": list(envelope),
                "envelope_source": envelope_source,
                "clearance_mm": clearance,
                "margin_width_mm": _r(margin_w),
                "margin_depth_mm": _r(margin_h),
            },
            roles=("pcb", "enclosure"),
            parameters=("pcb_outline", "pcb_clearance"),
        )

    # 2. The board outline is the agreed one.
    outline_p = c.parameter("pcb_outline")
    plain = "The circuit board must keep the agreed size"
    label = "PCB outline matches the contract"
    agreed = _wh(outline_p.value) if outline_p and outline_p.known else None
    missing = []
    if agreed is None:
        missing.append(b.missing_param(outline_p, "pcb_outline"))
    if pcb is None:
        missing.append(b.missing_item("pcb"))
    elif board is None:
        missing.append(f"{pcb.ref}: no board outline recorded")
    if missing or agreed is None or board is None:
        b.not_evaluated(
            "pcb_outline_matches",
            label,
            plain,
            missing,
            roles=("pcb",),
            parameters=("pcb_outline",),
        )
    else:
        tol = outline_p.tolerance if outline_p and outline_p.tolerance is not None else 0.0
        dw, dh = board[0] - agreed[0], board[1] - agreed[1]
        ok = abs(dw) <= tol and abs(dh) <= tol
        b.add(
            "pcb_outline_matches",
            label,
            plain,
            CheckOutcome.SATISFIED if ok else CheckOutcome.VIOLATED,
            f"Board {board[0]:g} x {board[1]:g} mm against agreed {agreed[0]:g} x "
            f"{agreed[1]:g} mm (tolerance {tol:g} mm)",
            values={
                "board_mm": list(board),
                "agreed_mm": list(agreed),
                "tolerance_mm": tol,
                "delta_mm": [_r(dw), _r(dh)],
            },
            roles=("pcb",),
            parameters=("pcb_outline",),
        )

    # 3. Mounting holes sit on the bosses, at the agreed diameter.
    plain = "The board's screw holes must line up with the base's posts"
    label = "Mounting hole positions match the bosses"
    holes = [p for p in (_xy(h) for h in pcb_meta.get("mounting_holes") or []) if p]
    bosses_raw = enc_meta.get("mounting_standoffs") or enc_meta.get("mounting_bosses") or []
    bosses = [p for p in (_xy(s) for s in bosses_raw) if p]
    missing = []
    if pcb is None:
        missing.append(b.missing_item("pcb"))
    elif not holes:
        missing.append(f"{pcb.ref}: no mounting holes recorded")
    if enc is None:
        missing.append(b.missing_item("enclosure"))
    elif not bosses:
        missing.append(f"{enc.ref}: no mounting bosses recorded")
    if missing:
        b.not_evaluated(
            "mounting_holes_match",
            label,
            plain,
            missing,
            roles=("pcb", "enclosure"),
            parameters=("mounting_hole_positions", "mounting_hole_diameter"),
        )
    else:
        pos_p = c.parameter("mounting_hole_positions")
        tol = (
            pos_p.tolerance
            if pos_p and pos_p.tolerance is not None
            else _num(enc_meta.get("mounting_tolerance")) or 0.5
        )
        unmatched = []
        for hole in holes:
            nearest = min(bosses, key=lambda s: _dist(hole, s))
            d = _dist(hole, nearest)
            if d > tol:
                unmatched.append(
                    {"hole": list(hole), "nearest_boss": list(nearest), "distance_mm": _r(d)}
                )
        dia_p = c.parameter("mounting_hole_diameter")
        dia_issues = []
        if dia_p and dia_p.known and _num(dia_p.value) is not None:
            want = float(_num(dia_p.value) or 0.0)
            dtol = dia_p.tolerance or 0.0
            for raw in pcb_meta.get("mounting_holes") or []:
                got = _num(raw.get("diameter")) if isinstance(raw, dict) else None
                if got is not None and abs(got - want) > dtol:
                    dia_issues.append({"hole": _xy(raw), "diameter_mm": got, "agreed_mm": want})
        ok = not unmatched and not dia_issues and len(holes) <= len(bosses)
        b.add(
            "mounting_holes_match",
            label,
            plain,
            CheckOutcome.SATISFIED if ok else CheckOutcome.VIOLATED,
            (
                f"All {len(holes)} holes sit on a boss within {tol:g} mm"
                if ok
                else f"{len(unmatched)} of {len(holes)} holes miss a boss by more than {tol:g} mm"
                + (f"; {len(dia_issues)} hole diameters differ" if dia_issues else "")
                + ("; more holes than bosses" if len(holes) > len(bosses) else "")
            ),
            values={
                "holes": len(holes),
                "bosses": len(bosses),
                "tolerance_mm": tol,
                "unmatched": unmatched,
                "diameter_issues": dia_issues,
            },
            roles=("pcb", "enclosure"),
            parameters=("mounting_hole_positions", "mounting_hole_diameter"),
        )

    # 4. The USB-C connector meets its cut-out.
    plain = "The USB-C socket must line up with the opening in the base"
    label = "USB-C connector position matches the cut-out"
    connectors = [
        cn
        for cn in pcb_meta.get("connectors") or []
        if isinstance(cn, dict) and "USB" in _norm(cn.get("name") or cn.get("type"))
    ]
    cutouts = [
        cu
        for cu in enc_meta.get("cutouts") or []
        if isinstance(cu, dict) and "USB" in _norm(cu.get("connector_name") or cu.get("name"))
    ]
    missing = []
    if pcb is None:
        missing.append(b.missing_item("pcb"))
    elif not connectors:
        missing.append(f"{pcb.ref}: no USB-C connector recorded")
    if enc is None:
        missing.append(b.missing_item("enclosure"))
    elif not cutouts:
        missing.append(f"{enc.ref}: no USB-C cut-out recorded")
    conn_xy = _xy(connectors[0]) if connectors else None
    cut_xy = _xy(cutouts[0]) if cutouts else None
    if not missing and (conn_xy is None or cut_xy is None):
        missing.append("connector or cut-out position (x, y) not recorded")
    if missing or conn_xy is None or cut_xy is None:
        b.not_evaluated(
            "usb_c_matches_cutout",
            label,
            plain,
            missing,
            roles=("pcb", "enclosure"),
            parameters=("usb_c_position", "usb_c_cutout"),
        )
    else:
        pos_p = c.parameter("usb_c_position")
        tol = pos_p.tolerance if pos_p and pos_p.tolerance is not None else 0.5
        offset = _dist(conn_xy, cut_xy)
        issues = []
        if offset > tol:
            issues.append(f"centres {offset:.2f} mm apart (tolerance {tol:g} mm)")
        cut_wh = _wh(cutouts[0])
        conn_wh = _wh(connectors[0])
        if cut_wh and conn_wh and (cut_wh[0] < conn_wh[0] or cut_wh[1] < conn_wh[1]):
            issues.append(
                f"cut-out {cut_wh[0]:g} x {cut_wh[1]:g} mm is smaller than the connector "
                f"{conn_wh[0]:g} x {conn_wh[1]:g} mm"
            )
        size_p = c.parameter("usb_c_cutout")
        agreed = _wh(size_p.value) if size_p and size_p.known else None
        if agreed and cut_wh:
            stol = size_p.tolerance if size_p and size_p.tolerance is not None else 0.0
            if abs(cut_wh[0] - agreed[0]) > stol or abs(cut_wh[1] - agreed[1]) > stol:
                issues.append(
                    f"cut-out {cut_wh[0]:g} x {cut_wh[1]:g} mm, agreed {agreed[0]:g} x "
                    f"{agreed[1]:g} mm"
                )
        b.add(
            "usb_c_matches_cutout",
            label,
            plain,
            CheckOutcome.VIOLATED if issues else CheckOutcome.SATISFIED,
            "; ".join(issues) if issues else f"Centres within {tol:g} mm and the cut-out clears it",
            values={
                "connector_xy_mm": list(conn_xy),
                "cutout_xy_mm": list(cut_xy),
                "offset_mm": _r(offset),
                "tolerance_mm": tol,
                "cutout_mm": list(cut_wh) if cut_wh else None,
                "connector_mm": list(conn_wh) if conn_wh else None,
            },
            roles=("pcb", "enclosure"),
            parameters=("usb_c_position", "usb_c_cutout"),
        )

    # 5. The tallest part stays under the agreed height.
    plain = "The parts on the board must stay low enough for the base"
    label = "Tallest component within the agreed maximum height"
    height_p = c.parameter("max_component_height")
    limit = _num(height_p.value) if height_p and height_p.known else None
    tallest = _num(pcb_meta.get("component_max_height")) if pcb else None
    missing = []
    if limit is None:
        missing.append(b.missing_param(height_p, "max_component_height"))
    if pcb is None:
        missing.append(b.missing_item("pcb"))
    elif tallest is None:
        missing.append(f"{pcb.ref}: no component_max_height recorded")
    if missing or limit is None or tallest is None:
        b.not_evaluated(
            "component_height",
            label,
            plain,
            missing,
            roles=("pcb",),
            parameters=("max_component_height",),
        )
    else:
        ok = tallest <= limit
        b.add(
            "component_height",
            label,
            plain,
            CheckOutcome.SATISFIED if ok else CheckOutcome.VIOLATED,
            f"Tallest component {tallest:g} mm against {limit:g} mm",
            values={"tallest_mm": tallest, "limit_mm": limit, "margin_mm": _r(limit - tallest)},
            roles=("pcb",),
            parameters=("max_component_height",),
        )

    # 6. The LED sits where the head expects it.
    plain = "The light must sit where the lamp head expects it"
    label = "LED position matches the contract"
    led_p = c.parameter("led_position")
    agreed_led = _xyz(led_p.value) if led_p and led_p.known else None
    led_holder = b.item("led_board") or pcb
    actual_led = _xyz(led_holder.metadata.get("led_position")) if led_holder else None
    missing = []
    if agreed_led is None:
        missing.append(b.missing_param(led_p, "led_position"))
    if led_holder is None:
        missing.append(b.missing_item("pcb"))
    elif actual_led is None:
        missing.append(f"{led_holder.ref}: no led_position recorded")
    roles = ("led_board",) if b.item("led_board") else ("pcb",)
    if missing or agreed_led is None or actual_led is None:
        b.not_evaluated(
            "led_position", label, plain, missing, roles=roles, parameters=("led_position",)
        )
    else:
        tol = led_p.tolerance if led_p and led_p.tolerance is not None else 0.0
        d = _dist(actual_led, agreed_led)
        b.add(
            "led_position",
            label,
            plain,
            CheckOutcome.SATISFIED if d <= tol else CheckOutcome.VIOLATED,
            f"LED {d:.2f} mm from the agreed position (tolerance {tol:g} mm)",
            values={
                "actual_mm": list(actual_led),
                "agreed_mm": list(agreed_led),
                "distance_mm": _r(d),
                "tolerance_mm": tol,
            },
            roles=roles,
            parameters=("led_position",),
        )


# ---------------------------------------------------------------------------
# EDA to firmware
# ---------------------------------------------------------------------------


def _pins_of(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else [value]
    out = []
    for row in rows:
        if isinstance(row, dict) and (row.get("pin") or row.get("net")):
            out.append({"pin": str(row.get("pin") or ""), "net": str(row.get("net") or "")})
    return out


def _pin_check(b: _Builder, wanted: list[dict[str, str]]) -> tuple[list[dict[str, Any]], int]:
    """Each wanted ``{pin, net}`` against the pin map: issues and how many matched."""
    rows = b.inputs.pinmap
    issues: list[dict[str, Any]] = []
    matched = 0
    for want in wanted:
        pin, net = _norm(want["pin"]), _norm(want["net"])
        on_net = sorted({_norm(r.get("pin")) for r in rows if net and _norm(r.get("net")) == net})
        on_pin = sorted(
            {str(r.get("net") or "") for r in rows if pin and _norm(r.get("pin")) == pin}
        )
        if net and on_net:
            if not pin or pin in on_net:
                matched += 1
            else:
                issues.append(
                    {"net": want["net"], "contract_pin": want["pin"], "netlist_pins": on_net}
                )
        elif pin and on_pin and not net:
            matched += 1
        else:
            issues.append(
                {
                    "net": want["net"],
                    "contract_pin": want["pin"],
                    "netlist_nets_on_pin": on_pin,
                    "issue": "not in the pin map",
                }
            )
    return issues, matched


def _eda_firmware(b: _Builder) -> None:
    c = b.contract
    pinmap, firmware = b.item("pinmap"), b.item("firmware")
    schematic = b.item("schematic")

    # 1. The controller is the agreed part on both sides.
    plain = "The brightness control must run on the chosen controller chip"
    label = "Controller part matches on the board and in the firmware"
    ctrl_p = c.parameter("controller")
    want = str(ctrl_p.value) if ctrl_p and ctrl_p.known else ""
    sides: dict[str, str] = {}
    for role, snap, keys in (
        ("pinmap", pinmap, ("controller", "mcu", "part")),
        ("schematic", schematic, ("controller", "mcu")),
        ("firmware", firmware, ("controller", "mcu", "target", "board")),
    ):
        if snap is None:
            continue
        found = next((str(snap.metadata[k]) for k in keys if snap.metadata.get(k)), "")
        if found:
            sides[role] = found
    missing = []
    if not want:
        missing.append(b.missing_param(ctrl_p, "controller"))
    if "firmware" not in sides:
        missing.append(
            b.missing_item("firmware")
            if firmware is None
            else f"{firmware.ref}: no controller or target recorded"
        )
    if "pinmap" not in sides and "schematic" not in sides:
        missing.append(
            b.missing_item("pinmap")
            if pinmap is None and schematic is None
            else "the board's controller part is not recorded"
        )
    if missing:
        b.not_evaluated(
            "controller_matches",
            label,
            plain,
            missing,
            roles=("pinmap", "schematic", "firmware"),
            parameters=("controller",),
        )
    else:
        wrong = {k: v for k, v in sides.items() if _norm(want) not in _norm(v)}
        b.add(
            "controller_matches",
            label,
            plain,
            CheckOutcome.VIOLATED if wrong else CheckOutcome.SATISFIED,
            (f"Not the agreed {want}: " + ", ".join(f"{k} {v}" for k, v in wrong.items()))
            if wrong
            else f"Every side names {want}",
            values={"agreed": want, "found": sides},
            roles=("pinmap", "schematic", "firmware"),
            parameters=("controller",),
        )

    # 2. The contract's pins are those nets in the EDA pin map.
    plain = "The board's wiring must use the agreed pins"
    label = "LED PWM and control input pins match the EDA pin map"
    pwm_p, inputs_p = c.parameter("led_pwm_pin"), c.parameter("control_input_pins")
    wanted = (_pins_of(pwm_p.value) if pwm_p and pwm_p.known else []) + (
        _pins_of(inputs_p.value) if inputs_p and inputs_p.known else []
    )
    missing = []
    if not (pwm_p and pwm_p.known):
        missing.append(b.missing_param(pwm_p, "led_pwm_pin"))
    if not (inputs_p and inputs_p.known):
        missing.append(b.missing_param(inputs_p, "control_input_pins"))
    if pinmap is None:
        missing.append(b.missing_item("pinmap"))
    elif not b.inputs.pinmap:
        missing.append(f"{pinmap.ref}: no pins could be read")
    if missing:
        b.not_evaluated(
            "pins_match_netlist",
            label,
            plain,
            missing,
            roles=("pinmap",),
            parameters=("led_pwm_pin", "control_input_pins"),
        )
    else:
        issues, matched = _pin_check(b, wanted)
        b.add(
            "pins_match_netlist",
            label,
            plain,
            CheckOutcome.VIOLATED if issues else CheckOutcome.SATISFIED,
            f"{matched} of {len(wanted)} contract pins are on their nets in the pin map",
            values={"wanted": wanted, "issues": issues},
            roles=("pinmap",),
            parameters=("led_pwm_pin", "control_input_pins"),
        )

    # 3. The firmware uses those pins (the pin_maps_to proposals' evidence).
    plain = "The brightness control's software must use the same pins"
    label = "Firmware uses the contract's pins"
    missing = []
    if not wanted:
        missing.append("agreed pins: unknown, owned by electronics")
    if pinmap is None:
        missing.append(b.missing_item("pinmap"))
    if firmware is None:
        missing.append(b.missing_item("firmware"))
    if pinmap is not None and firmware is not None and not b.inputs.pin_edge:
        missing.append(
            f"no pin_maps_to link between {pinmap.ref} and {firmware.ref} yet "
            "(propose interfaces to infer it from the sources)"
        )
    if missing:
        b.not_evaluated(
            "firmware_uses_pins",
            label,
            plain,
            missing,
            roles=("pinmap", "firmware"),
            parameters=("led_pwm_pin", "control_input_pins"),
        )
    else:
        named = {_norm(n) for n in b.inputs.firmware_pins}
        unused = [
            w for w in wanted if _norm(w["pin"]) not in named and _norm(w["net"]) not in named
        ]
        b.add(
            "firmware_uses_pins",
            label,
            plain,
            CheckOutcome.VIOLATED if unused else CheckOutcome.SATISFIED,
            f"The firmware does not name {len(unused)} of {len(wanted)} contract pins"
            if unused
            else f"The firmware names all {len(wanted)} contract pins",
            values={"unused": unused, "firmware_names": sorted(b.inputs.firmware_pins)},
            roles=("pinmap", "firmware"),
            parameters=("led_pwm_pin", "control_input_pins"),
        )

    # 4. The PWM frequency the firmware sets is within the driver's range.
    plain = "The dimming speed must suit the LED driver"
    label = "Firmware PWM frequency within the driver's range"
    range_p = c.parameter("pwm_frequency_range")
    rng = range_p.value if range_p and range_p.known and isinstance(range_p.value, dict) else None
    lo, hi = (_num(rng.get("min")), _num(rng.get("max"))) if rng else (None, None)
    freq = _num(firmware.metadata.get("pwm_frequency_hz")) if firmware else None
    missing = []
    if lo is None or hi is None:
        missing.append(b.missing_param(range_p, "pwm_frequency_range"))
    if firmware is None:
        missing.append(b.missing_item("firmware"))
    elif freq is None:
        missing.append(f"{firmware.ref}: no pwm_frequency_hz recorded")
    if missing or lo is None or hi is None or freq is None:
        b.not_evaluated(
            "pwm_frequency",
            label,
            plain,
            missing,
            roles=("firmware",),
            parameters=("pwm_frequency_range",),
        )
    else:
        ok = lo <= freq <= hi
        b.add(
            "pwm_frequency",
            label,
            plain,
            CheckOutcome.SATISFIED if ok else CheckOutcome.VIOLATED,
            f"{freq:g} Hz against {lo:g} to {hi:g} Hz",
            values={"pwm_hz": freq, "min_hz": lo, "max_hz": hi},
            roles=("firmware",),
            parameters=("pwm_frequency_range",),
        )

    # 5. The LED current the board sets is within the driver's limit.
    plain = "The light must not draw more current than the driver allows"
    label = "LED current within the driver's maximum"
    cur_p = c.parameter("max_led_current")
    limit = _num(cur_p.value) if cur_p and cur_p.known else None
    holder = schematic or b.item("pcb")
    current = _num(holder.metadata.get("led_current_ma")) if holder else None
    missing = []
    if limit is None:
        missing.append(b.missing_param(cur_p, "max_led_current"))
    if holder is None:
        missing.append(b.missing_item("schematic"))
    elif current is None:
        missing.append(f"{holder.ref}: no led_current_ma recorded")
    if missing or limit is None or current is None:
        b.not_evaluated(
            "led_current",
            label,
            plain,
            missing,
            roles=("schematic",),
            parameters=("max_led_current",),
        )
    else:
        b.add(
            "led_current",
            label,
            plain,
            CheckOutcome.SATISFIED if current <= limit else CheckOutcome.VIOLATED,
            f"{current:g} mA against {limit:g} mA",
            values={"led_current_ma": current, "limit_ma": limit},
            roles=("schematic",),
            parameters=("max_led_current",),
        )


def interface_version_ref(contract_key: str, interface_version: int) -> str:
    """How a firmware build names the interface version it was built against."""
    return f"{contract_key} v{interface_version}"


def _firmware_interface_version(b: _Builder, contract_key: str, interface_version: int) -> None:
    """The firmware was built against this interface version of the contract."""
    firmware = b.item("firmware")
    plain = "The software must be built for the current wiring agreement"
    label = "Firmware built against the current interface version"
    current = interface_version_ref(contract_key, interface_version)
    if firmware is None:
        b.not_evaluated(
            "interface_version", label, plain, [b.missing_item("firmware")], roles=("firmware",)
        )
        return
    built = firmware.metadata.get("interface_version")
    if not built:
        b.not_evaluated(
            "interface_version",
            label,
            plain,
            [f"{firmware.ref}: does not record the interface version it was built against"],
            roles=("firmware",),
        )
        return
    b.add(
        "interface_version",
        label,
        plain,
        CheckOutcome.SATISFIED if built == current else CheckOutcome.STALE,
        f"Built against {built}; the contract is {current}",
        values={"built_against": built, "current": current},
        roles=("firmware",),
    )


# ---------------------------------------------------------------------------
# CAD and EDA to simulation
# ---------------------------------------------------------------------------


def _known_param(c: InterfaceContract, name: str) -> CoupledParameter | None:
    p = c.parameter(name)
    return p if p is not None and p.known else None


def _simulation(b: _Builder) -> None:
    c = b.contract
    geometry_roles = tuple(r for r in ("enclosure", "head", "arm") if b.item(r))

    plain = "The temperature check needs the lamp's shape"
    label = "Geometry revision available for the analysis"
    if geometry_roles:
        refs = [snap.ref for r in geometry_roles if (snap := b.item(r))]
        b.add(
            "geometry_revision",
            label,
            plain,
            CheckOutcome.SATISFIED,
            "Analysis geometry: " + ", ".join(refs),
            values={"geometry": refs},
            roles=geometry_roles,
        )
    else:
        b.not_evaluated(
            "geometry_revision",
            label,
            plain,
            [b.missing_item(r) for r in ("enclosure", "head") if c.link(r)]
            or ["no CAD geometry linked"],
        )

    plain = "The temperature check needs the materials"
    label = "Materials stated or assumed for the analysis"
    material = b.inputs.context.get("selected_material") or {}
    mat_param = _known_param(c, "materials")
    if material.get("value") and material.get("authority") not in (None, "unknown"):
        b.add(
            "materials",
            label,
            plain,
            CheckOutcome.SATISFIED,
            f"Material {material['value']} ({material.get('authority')})",
            values={"material": material.get("value"), "authority": material.get("authority")},
            extra_inputs=[b.inputs.context_ref] if b.inputs.context_ref else [],
            parameters=("materials",),
        )
    elif mat_param is not None:
        b.add(
            "materials",
            label,
            plain,
            CheckOutcome.SATISFIED,
            f"Materials {mat_param.value} ({mat_param.basis.value})",
            values={"materials": mat_param.value, "basis": mat_param.basis.value},
            parameters=("materials",),
        )
    else:
        b.not_evaluated(
            "materials",
            label,
            plain,
            ["selected material: unknown in the manufacturing context and the contract"],
            parameters=("materials",),
        )

    plain = "The temperature check needs to know how much heat the light makes"
    label = "LED electrical power and heat estimate present"
    power = _known_param(c, "led_electrical_power")
    heat = _known_param(c, "led_heat_estimate")
    board = b.item("pcb") or b.item("led_board")
    board_power = _num(board.metadata.get("led_power_w")) if board else None
    if heat is not None and (power is not None or board_power is not None):
        b.add(
            "led_heat_estimate",
            label,
            plain,
            CheckOutcome.SATISFIED,
            f"Heat {heat.value} {heat.unit} ({heat.basis.value})",
            values={
                "led_power": power.value if power else board_power,
                "heat": heat.value,
                "basis": heat.basis.value,
            },
            roles=("pcb",) if board_power is not None else (),
            parameters=("led_electrical_power", "led_heat_estimate"),
        )
    else:
        missing = []
        if power is None and board_power is None:
            missing.append(
                "LED electrical power: unknown (set by the LED module selection in electronics)"
            )
        if heat is None:
            missing.append("LED heat estimate: unknown (derived from the LED power and efficacy)")
        b.not_evaluated(
            "led_heat_estimate",
            label,
            plain,
            missing,
            parameters=("led_electrical_power", "led_heat_estimate"),
        )

    plain = "The temperature check needs the room conditions"
    label = "Boundary conditions stated or assumed"
    ambient, convection = _known_param(c, "ambient_temperature"), _known_param(c, "convection")
    if ambient is not None and convection is not None:
        b.add(
            "boundary_conditions",
            label,
            plain,
            CheckOutcome.SATISFIED,
            f"Ambient {ambient.value} {ambient.unit} ({ambient.basis.value}); convection "
            f"{convection.value} ({convection.basis.value})",
            values={
                "ambient": ambient.value,
                "ambient_basis": ambient.basis.value,
                "convection": convection.value,
                "convection_basis": convection.basis.value,
            },
            parameters=("ambient_temperature", "convection"),
        )
    else:
        b.not_evaluated(
            "boundary_conditions",
            label,
            plain,
            [
                b.missing_param(c.parameter(n), n)
                for n, v in (("ambient_temperature", ambient), ("convection", convection))
                if v is None
            ],
            parameters=("ambient_temperature", "convection"),
        )


# ---------------------------------------------------------------------------
# CAD and EDA to manufacturing
# ---------------------------------------------------------------------------


def _artifact_check(b: _Builder, artifact: ExpectedArtifact, part: str | None) -> None:
    label = f"{artifact.label}" + (f" for {part}" if part else "")
    plain = f"The {artifact.label.lower()}" + (f" for {part}" if part else "") + " must be ready"
    check_id = f"artifact_{artifact.kind}" + (f"_{part}" if part else "")
    found = list(b.inputs.artifacts.get(artifact.kind) or [])
    if part:
        found = [a for a in found if a.component_id == part]
    if artifact.file_hint:
        found = [a for a in found if any(artifact.file_hint in f.lower() for f in a.files)]
    if not found:
        b.not_evaluated(
            check_id,
            label,
            plain,
            [f"{label}: not produced yet (produced by {artifact.produced_by})"],
        )
        return
    newest = found[-1]
    moved = [p for p in newest.pins if p.get("current") and p.get("pinned") != p.get("current")]
    if moved:
        b.add(
            check_id,
            label,
            plain,
            CheckOutcome.STALE,
            f"{newest.ref} was made from "
            + ", ".join(f"{p['pinned']} (now {p['current']})" for p in moved),
            values={"artifact": newest.ref, "moved": moved, "rule": artifact.rule},
            extra_inputs=[newest.ref],
        )
    elif newest.status in ("FAIL",):
        b.add(
            check_id,
            label,
            plain,
            CheckOutcome.VIOLATED,
            f"{newest.ref} records a failed result",
            values={"artifact": newest.ref, "status": newest.status},
            extra_inputs=[newest.ref],
        )
    elif not newest.pins:
        b.not_evaluated(
            check_id,
            label,
            plain,
            [f"{newest.ref}: records no source revision, so its compatibility is unknown"],
        )
    else:
        b.add(
            check_id,
            label,
            plain,
            CheckOutcome.SATISFIED,
            f"{newest.ref} was made from the current "
            + ", ".join(str(p.get("current")) for p in newest.pins),
            values={"artifact": newest.ref, "made_from": [p.get("pinned") for p in newest.pins]},
            extra_inputs=[newest.ref],
        )


def _manufacturing(b: _Builder) -> None:
    for artifact in b.contract.artifacts:
        if artifact.parts:
            for part in artifact.parts:
                _artifact_check(b, artifact, part)
        else:
            _artifact_check(b, artifact, None)


# ---------------------------------------------------------------------------
# The evaluation
# ---------------------------------------------------------------------------


def _rollup(checks: list[ContractCheck]) -> CheckOutcome:
    outcomes = {c.outcome for c in checks}
    if CheckOutcome.VIOLATED in outcomes:
        return CheckOutcome.VIOLATED
    if CheckOutcome.STALE in outcomes:
        return CheckOutcome.STALE
    if checks and outcomes == {CheckOutcome.SATISFIED}:
        return CheckOutcome.SATISFIED
    return CheckOutcome.NOT_EVALUATED


def _summary(evaluation: ContractEvaluation) -> str:
    n = evaluation.counts()
    total = len(evaluation.checks)
    parts = [f"{n[o.value]} {o.value.replace('_', ' ')}" for o in CheckOutcome if n[o.value]]
    return (
        f"{total} check{'s' if total != 1 else ''}: " + ", ".join(parts) if total else "no checks"
    )


def evaluate_contract(
    contract: InterfaceContract,
    *,
    contract_key: str,
    contract_revision: int,
    inputs: CheckInputs,
    interface_version: int = 1,
) -> ContractEvaluation:
    """Every check of ``contract`` against ``inputs``. Pure; never raises on data."""
    b = _Builder(contract, inputs)
    if contract.kind is ContractKind.CAD_EDA:
        _cad_eda(b)
    elif contract.kind is ContractKind.EDA_FIRMWARE:
        _eda_firmware(b)
        _firmware_interface_version(b, contract_key, interface_version)
    elif contract.kind is ContractKind.SIMULATION:
        _simulation(b)
    else:
        _manufacturing(b)
    own = f"{contract_key}@{contract_revision}"
    read = [own]
    for check in b.checks:
        for ref in check.inputs:
            if ref and ref not in read:
                read.append(ref)
    evaluation = ContractEvaluation(
        contract_key=contract_key,
        contract_revision=contract_revision,
        interface_version=interface_version,
        kind=contract.kind,
        outcome=_rollup(b.checks),
        checks=b.checks,
        inputs=read,
        evaluated_at=datetime.now(UTC).isoformat(),
    )
    evaluation.summary = _summary(evaluation)
    return evaluation


def validation_status_of(evaluation: ContractEvaluation) -> ValidationStatus:
    """The shared validation vocabulary (CHG-005) for an evaluation.

    Satisfied is ``PASS``, violated ``FAIL``, stale ``STALE``. Not evaluated is
    ``NOT_EVALUATED`` when no check could run and ``INCOMPLETE`` when some did:
    a partial check is never a pass.
    """
    if evaluation.outcome is CheckOutcome.SATISFIED:
        return ValidationStatus.PASS
    if evaluation.outcome is CheckOutcome.VIOLATED:
        return ValidationStatus.FAIL
    if evaluation.outcome is CheckOutcome.STALE:
        return ValidationStatus.STALE
    ran = any(c.outcome is not CheckOutcome.NOT_EVALUATED for c in evaluation.checks)
    return ValidationStatus.INCOMPLETE if ran else ValidationStatus.NOT_EVALUATED


def agreed_domains(agreements: list[ContractAgreement], interface_version: int) -> set[str]:
    """The owning domains that agreed to this interface version."""
    return {a.domain for a in agreements if a.interface_version == interface_version}


def contract_status(
    contract: InterfaceContract,
    *,
    contract_revision: int,
    interface_version: int,
    agreements: list[ContractAgreement],
    latest: ContractEvaluation | None,
    latest_is_stale: bool = False,
) -> ContractStatus:
    """Where a contract stands: its latest check first, then its agreement.

    A violation found on current revisions is ``violated``; a check that read
    revisions which have since moved (or a check of an older contract
    revision) is ``stale``. Otherwise the contract is ``agreed`` once every
    owning domain agreed to this interface version, and ``proposed`` until then.
    """
    if latest is not None:
        old = latest.contract_revision != contract_revision
        if latest_is_stale or old or latest.outcome is CheckOutcome.STALE:
            return ContractStatus.STALE
        if latest.outcome is CheckOutcome.VIOLATED:
            return ContractStatus.VIOLATED
    if set(contract.owning_domains) <= agreed_domains(agreements, interface_version):
        return ContractStatus.AGREED
    return ContractStatus.PROPOSED


#: What Create says for each state, in plain words.
_WORDS: dict[str, str] = {
    "satisfied": "checked, it fits",
    "violated": "does not fit yet",
    "not_evaluated": "not checked yet",
    "stale": "needs checking again after a change",
    "unchecked": "not checked yet",
}


def outcome_words(outcome: CheckOutcome | str | None) -> str:
    """The plain words Create uses for a check outcome (``None``: never checked)."""
    key = getattr(outcome, "value", outcome) or "unchecked"
    return _WORDS.get(str(key), "not checked yet")
