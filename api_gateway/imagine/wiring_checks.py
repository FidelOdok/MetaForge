"""Wiring checks for a studio's connection list (FORGE-886).

FORGE-883 made the wiring data: ``context.connections`` joins the real parts' interfaces, and
each write already refuses an end that does not exist and warns on mismatched buses, two
drivers, a net with no driver and a signal too weak for its input. This module asks the
questions a list of single connections cannot answer on its own:

- **Unwired power.** A part's power or ground input that no connection reaches.
- **Floating inputs.** A signal input that no connection drives. An interface can be left
  unconnected on purpose by listing it in ``context.connections.unused`` (``part.interface``).
- **Wire size.** A power connection whose ``wire.awg`` is too thin for the peak current it
  carries, with the same 25 % headroom and ratings the template engine's harness uses.

Outputs, bidirectional signals (a bus, a GPIO) and parts left out of the build are not
findings when unwired: a board has many spare pins. A studio with no connection list has no
findings here; the handover item says the wiring is not recorded instead.

Whether the wires fit inside the enclosure is measured in the page, where the geometry is.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.wiring_checks")

#: gauge, rated amps; the template engine's harness table (engine.js ``AWG``).
AWG_RATING: dict[int, float] = {28: 1.4, 26: 2.2, 24: 3.5, 22: 7.0}
#: A wire carries its peak current with this much headroom.
HEADROOM = 1.25


def _block(context: dict[str, Any]) -> dict[str, Any]:
    block = context.get("connections")
    return block if isinstance(block, dict) else {}


def connections_of(context: dict[str, Any]) -> list[dict[str, Any]]:
    """The studio's connections; empty when it records none."""
    conns = _block(context).get("connections")
    return [c for c in conns if isinstance(c, dict)] if isinstance(conns, list) else []


def _parts(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    comps = context.get("components")
    parts = comps.get("parts") if isinstance(comps, dict) else None
    return {
        str(p["id"]): p
        for p in parts or []
        if isinstance(p, dict) and p.get("id") and not p.get("excluded")
    }


def _iface_ref(ref: object) -> str | None:
    bits = str(ref).split(".") if isinstance(ref, str) else []
    return ".".join(bits[:2]) if len(bits) >= 2 else None


def _iface(parts: dict[str, dict[str, Any]], ref: str | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    pid, _, iid = ref.partition(".")
    for f in (parts.get(pid) or {}).get("interfaces") or []:
        if isinstance(f, dict) and f.get("id") == iid:
            return f
    return None


def wiring_findings(context: dict[str, Any]) -> list[str]:
    """What is wrong with the wiring as a whole; empty when there is no connection list."""
    conns = connections_of(context)
    if not conns:
        return []
    with tracer.start_as_current_span("imagine.wiring_checks") as span:
        parts = _parts(context)
        unused = {str(u) for u in _block(context).get("unused") or [] if isinstance(u, str)}
        wired: set[str] = set()
        for c in conns:
            for side in ("from", "to"):
                ref = _iface_ref(c.get(side))
                if ref:
                    wired.add(ref)
        out: list[str] = []
        for pid, part in parts.items():
            for f in part.get("interfaces") or []:
                if not isinstance(f, dict) or not f.get("id"):
                    continue
                ref = f"{pid}.{f['id']}"
                if ref in wired or ref in unused:
                    continue
                if f.get("bus") == "pwr" and f.get("dir") in ("in", "bi"):
                    out.append(
                        f'Wiring: "{ref}" is a power input that no connection reaches; '
                        "wire it, or list it in connections.unused if it is left off on purpose."
                    )
                elif f.get("dir") == "in":
                    out.append(
                        f'Wiring: "{ref}" is an input that nothing drives (it floats); '
                        "wire it, tie it, or list it in connections.unused."
                    )
        for c in conns:
            out.extend(_gauge_findings(c, parts))
        span.set_attribute("wiring.connections", len(conns))
        span.set_attribute("wiring.findings", len(out))
        logger.info("imagine_wiring_checked", connections=len(conns), findings=len(out))
        return out


def _gauge_findings(c: dict[str, Any], parts: dict[str, dict[str, Any]]) -> list[str]:
    wire = c.get("wire")
    awg = wire.get("awg") if isinstance(wire, dict) else None
    if awg is None:
        return []
    cid = str(c.get("id") or "?")
    if not isinstance(awg, int) or awg not in AWG_RATING:
        sizes = ", ".join(str(g) for g in AWG_RATING)
        return [f'Wiring: connection "{cid}" names {awg!r} AWG; the ratings cover {sizes} AWG.']
    ends = [_iface(parts, _iface_ref(c.get(s))) for s in ("from", "to")]
    if not any(e and e.get("bus") == "pwr" for e in ends):
        return []
    peaks = [
        float(e["imax_mA"])
        for e in ends
        if e and isinstance(e.get("imax_mA"), (int, float)) and e["imax_mA"] > 0
    ]
    if not peaks:
        return []
    amps = min(peaks) / 1000
    rated = AWG_RATING[awg]
    if amps * HEADROOM <= rated:
        return []
    fits = next(
        (g for g, a in sorted(AWG_RATING.items(), reverse=True) if a >= amps * HEADROOM), None
    )
    fix = f"use {fits} AWG" if fits is not None else "no gauge in the table carries it"
    return [
        f'Wiring: connection "{cid}" is {awg} AWG, rated {rated:g} A, for a {amps:g} A peak '
        f"({amps * HEADROOM:g} A with headroom); {fix}."
    ]


def checklist_item(context: dict[str, Any]) -> tuple[str, str]:
    """The handover item: ``(status, detail)``."""
    conns = connections_of(context)
    with_ifaces = [p for p in _parts(context).values() if p.get("interfaces")]
    if not conns:
        if len(with_ifaces) < 2:
            return "done", "Fewer than two parts with interfaces; nothing to wire."
        return (
            "assumed",
            f"No connection list; {len(with_ifaces)} parts with interfaces are wired in words "
            "only. Record context.connections so the wiring can be drawn and checked.",
        )
    findings = wiring_findings(context)
    if findings:
        more = f" (and {len(findings) - 3} more)" if len(findings) > 3 else ""
        return "missing", " ".join(f.removeprefix("Wiring: ") for f in findings[:3]) + more
    return "done", f"{len(conns)} connections; every power and signal input is wired."
