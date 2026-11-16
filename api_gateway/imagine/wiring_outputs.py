"""What Develop gets from a studio's connection list at handover (FORGE-887).

FORGE-883 made the wiring data and FORGE-886 checks it. At handover the same list becomes:

- **A firmware pin map.** One parameter per wired interface end (``pin_<part>_<interface>``):
  its net, what it joins and its bus and direction. They go in the proposed electronics and
  firmware contract (``IFC-EDA-FIRMWARE``), so firmware builds against named pins rather than
  words in a decision.
- **Harness BOM lines.** Hook-up wire per gauge with its cut lengths. A wire is sized like the
  page draws it and the checks judge it: ``wire.awg`` when the studio fixes one, else the
  thinnest gauge rated for the peak current with 25 % headroom, else 28 AWG for a signal. A
  length comes from the connection's ``length_mm`` when it records one, and is otherwise left
  to measure, never guessed.
- **A numbered wiring sequence.** Ground first, then power, then signals, then a continuity
  and short check before first power-up.

The KiCad interconnect schematic and lengths measured from the drawn form are later steps.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from api_gateway.imagine.wiring_checks import AWG_RATING, HEADROOM, connections_of
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.wiring_outputs")

#: Signals and power with no recorded current use the engine's defaults.
SIGNAL_AWG = 28
POWER_AWG = 26
#: Extra per wire for stripping and a service loop, as the page's drawing adds.
SLACK_MM = 15

_GROUND = re.compile(r"^(GND|VSS|0V)\b|GND$", re.IGNORECASE)


def _slug(text: str, n: int) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s or "x")[:n].strip("_") or "x"


def _parts(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    comps = context.get("components")
    parts = comps.get("parts") if isinstance(comps, dict) else None
    return {str(p["id"]): p for p in parts or [] if isinstance(p, dict) and p.get("id")}


def _end(parts: dict[str, dict[str, Any]], ref: object) -> tuple[dict[str, Any], dict[str, Any]]:
    bits = str(ref).split(".")
    part = parts.get(bits[0]) or {}
    iface = next(
        (
            f
            for f in part.get("interfaces") or []
            if isinstance(f, dict) and len(bits) > 1 and f.get("id") == bits[1]
        ),
        {},
    )
    return part, iface


def _kind(c: dict[str, Any], parts: dict[str, dict[str, Any]]) -> str:
    """``ground``, ``power`` or ``signal``: the order the wires go on."""
    if _GROUND.search(str(c.get("net") or "")):
        return "ground"
    ends = [_end(parts, c.get(s))[1] for s in ("from", "to")]
    return "power" if any(e.get("bus") == "pwr" for e in ends) else "signal"


def wire_gauge(c: dict[str, Any], parts: dict[str, dict[str, Any]]) -> tuple[int, str]:
    """The gauge for one connection and what decided it."""
    wire = c.get("wire")
    awg = wire.get("awg") if isinstance(wire, dict) else None
    if isinstance(awg, int) and awg in AWG_RATING:
        return awg, "set in the studio"
    ends = [_end(parts, c.get(s))[1] for s in ("from", "to")]
    if not any(e.get("bus") == "pwr" for e in ends):
        return SIGNAL_AWG, "signal"
    peaks = [
        float(e["imax_mA"])
        for e in ends
        if isinstance(e.get("imax_mA"), (int, float)) and e["imax_mA"] > 0
    ]
    if not peaks:
        return POWER_AWG, "power, no peak current recorded"
    amps = min(peaks) / 1000
    for g, rated in sorted(AWG_RATING.items(), reverse=True):
        if rated >= amps * HEADROOM:
            return g, f"{amps:g} A peak with 25 % headroom"
    return min(AWG_RATING), f"{amps:g} A peak; above the table, check the wire"


def _length(c: dict[str, Any]) -> float | None:
    v = c.get("length_mm")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def wiring_table(context: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per connection, in the order to wire them."""
    parts = _parts(context)
    order = {"ground": 0, "power": 1, "signal": 2}
    rows = []
    for i, c in enumerate(connections_of(context)):
        if not c.get("from") or not c.get("to"):
            continue
        awg, why = wire_gauge(c, parts)
        run = _length(c)
        rows.append(
            {
                "id": str(c.get("id") or f"c{i + 1}"),
                "net": str(c.get("net") or ""),
                "from": str(c["from"]),
                "to": str(c["to"]),
                "kind": _kind(c, parts),
                "awg": awg,
                "sized_from": why,
                "length_mm": run + SLACK_MM if run is not None else None,
                "_i": i,
            }
        )
    rows.sort(key=lambda r: (order[str(r["kind"])], r["_i"]))
    for r in rows:
        r.pop("_i")
    return rows


def pin_map(context: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per wired interface end: its net and what it joins."""
    parts = _parts(context)
    seen: dict[str, dict[str, Any]] = {}
    for c in connections_of(context):
        for side, other in (("from", "to"), ("to", "from")):
            ref = c.get(side)
            if not isinstance(ref, str) or "." not in ref:
                continue
            part, iface = _end(parts, ref)
            entry = seen.setdefault(
                ref,
                {
                    "ref": ref,
                    "part": ref.split(".")[0],
                    "part_name": str(part.get("name") or ref.split(".")[0]),
                    "net": str(c.get("net") or ""),
                    "bus": iface.get("bus"),
                    "dir": iface.get("dir"),
                    "to": [],
                },
            )
            if isinstance(c.get(other), str):
                entry["to"].append(str(c[other]))
    return list(seen.values())


def harness_lines(context: dict[str, Any]) -> list[dict[str, Any]]:
    """BOM lines for the hook-up wire, one per gauge, with the cut list."""
    by_gauge: dict[int, list[dict[str, Any]]] = {}
    for r in wiring_table(context):
        by_gauge.setdefault(r["awg"], []).append(r)
    lines = []
    for awg in sorted(by_gauge, reverse=True):
        rows = by_gauge[awg]
        known = [r["length_mm"] for r in rows if r["length_mm"] is not None]
        complete = len(known) == len(rows)
        lines.append(
            {
                "item": f"Hook-up wire, {awg} AWG",
                "cuts": len(rows),
                "qty_m": round(sum(known) / 1000, 3) if complete else None,
                "unit": "m",
                "spec": (
                    "Cut lengths from the wiring table, each with 15 mm for stripping"
                    if complete
                    else f"{len(rows) - len(known)} of {len(rows)} lengths still to measure "
                    "from the drawn form"
                ),
                "connections": [r["id"] for r in rows],
            }
        )
    return lines


def wiring_sequence(context: dict[str, Any]) -> list[str]:
    """Numbered steps: ground, power, signals, then the checks before power-up."""
    rows = wiring_table(context)
    if not rows:
        return []
    steps = []
    for r in rows:
        cut = f", cut {r['length_mm']:g} mm" if r["length_mm"] is not None else ""
        steps.append(
            f"Wire {r['net']} ({r['kind']}): {r['from']} to {r['to']}, {r['awg']} AWG{cut}."
        )
    steps.append("Check continuity of each net end to end, and that no pin is on the wrong net.")
    steps.append("Check there is no short between any power net and ground before first power-up.")
    return [f"{i}. {s}" for i, s in enumerate(steps, 1)]


def handover_record(context: dict[str, Any], product: str) -> tuple[str, str] | None:
    """The wiring decision's title and body; ``None`` when there is no connection list."""
    rows = wiring_table(context)
    if not rows:
        return None
    with tracer.start_as_current_span("imagine.wiring_outputs") as span:
        out = [
            f"The wiring of {product} as recorded in the Imagine studio. Nets and pins come "
            "from the parts' pinouts; gauges follow the harness rule. Nothing here is above V2.",
            "",
            "| # | Net | From | To | Wire | Length mm | Sized from |",
            "|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(rows, 1):
            length = f"{r['length_mm']:g}" if r["length_mm"] is not None else "to measure"
            out.append(
                f"| {i} | {r['net']} | {r['from']} | {r['to']} | {r['awg']} AWG | {length} "
                f"| {r['sized_from']} |"
            )
        out += ["", "Harness BOM:", ""]
        for line in harness_lines(context):
            qty = f"{line['qty_m']:g} m" if line["qty_m"] is not None else "length to measure"
            out.append(f"- {line['item']}: {line['cuts']} cuts, {qty}. {line['spec']}.")
        out += ["", "Wiring sequence:", "", *wiring_sequence(context)]
        span.set_attribute("wiring.connections", len(rows))
        logger.info("imagine_wiring_handover", connections=len(rows))
        return f"Imagine wiring: {product}", "\n".join(out)


def pin_parameter_specs(context: dict[str, Any]) -> list[dict[str, Any]]:
    """The pin map as contract parameters: name, label, value (the net) and a note."""
    specs = []
    for e in pin_map(context):
        bits = e["ref"].split(".")
        name = "pin_" + "_".join(_slug(b, 20) for b in bits[:3])
        specs.append(
            {
                "name": name[:64].strip("_"),
                "label": f"{e['part_name']}: {'.'.join(bits[1:])}"[:200],
                "value": e["net"],
                "note": (
                    f"net {e['net']} to {', '.join(e['to'])}; bus {e['bus'] or 'n/a'}, "
                    f"direction {e['dir'] or 'n/a'}; from the Imagine connection list (V2)"
                )[:500],
            }
        )
    return specs
