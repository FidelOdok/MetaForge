"""Hardware-design concrete agents (MET-474).

Implements the ``Planner`` / ``Generator`` / ``Evaluator`` Protocols
from ``orchestrator.harness.three_agent``. The agents are
deterministic — no LLM calls — so the harness runs end-to-end in CI
under a second. The LLM-driven variant lands as a drop-in
replacement when ``LLMProvider`` (MET-462) is threaded through the
orchestrator.

Pipeline:

1. ``HardwarePlanner`` reads ``HardwareUserIntent`` + any prior
   evaluator feedback, writes ``design_spec.md`` capturing
   requirements (rail voltage, peak current budget, peripherals).
2. ``HardwareGenerator`` parses the spec, picks components off a
   curated catalog, writes ``bom.csv`` + ``schematic_outline.md``.
3. ``HardwareEvaluator`` runs three programmatic gates:
   - ``bom_present`` — at least one MCU + one regulator
   - ``voltage_margin`` — regulator output ≥ MCU rail
   - ``power_budget`` — sum of peak currents ≤ regulator capacity

When the planner sees prior failed gates, it relaxes the budget
(picks a beefier regulator next iteration) and writes a new spec.
The loop converges in 1-2 iterations for the IoT scenario; the cap
of 5 from the shared ``HarnessConfig`` is plenty of headroom.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from orchestrator.harness.artifacts import ArtifactStore
from orchestrator.harness.three_agent import (
    EvaluatorResult,
    GateResult,
    GeneratorResult,
    PlannerResult,
)

# ---------------------------------------------------------------------------
# User intent — what the user types in
# ---------------------------------------------------------------------------


@dataclass
class HardwareUserIntent:
    """The product brief that kicks off a harness run."""

    description: str
    # Optional knobs — the planner falls back to sensible defaults
    # when these are None.
    target_rail_volts: float | None = None
    peripherals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Curated component catalog — minimal, deterministic, real MPNs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogPart:
    mpn: str
    description: str
    category: str  # "mcu" / "regulator" / "sensor" / "radio"
    rail_volts: float  # operating voltage
    peak_current_ma: float  # peak draw (regulator: capacity)


# A small but real catalog. Real prod plugs in the L1 KB +
# distributor adapters here; for the IoT acceptance scenario the
# catalog below is sufficient to converge on a working BOM.
_CATALOG: tuple[CatalogPart, ...] = (
    CatalogPart("ESP32-WROOM-32", "WiFi+BT MCU module", "mcu", 3.3, 240.0),
    CatalogPart("STM32H743VIT6", "ARM Cortex-M7 MCU", "mcu", 3.3, 280.0),
    CatalogPart("AMS1117-3.3", "3.3V LDO regulator (800 mA)", "regulator", 3.3, 800.0),
    CatalogPart("LM2596-3.3", "3.3V buck regulator (3 A)", "regulator", 3.3, 3000.0),
    CatalogPart("BME280", "Pressure+humidity+temp sensor", "sensor", 3.3, 0.4),
    CatalogPart("BMA400", "Accelerometer", "sensor", 3.3, 0.014),
    CatalogPart("nRF24L01", "2.4 GHz radio (extra link)", "radio", 3.3, 15.0),
)


_CATALOG_BY_MPN: dict[str, CatalogPart] = {p.mpn: p for p in _CATALOG}


# ---------------------------------------------------------------------------
# Spec format — keep it parseable so the generator + evaluator agree
# ---------------------------------------------------------------------------


_SPEC_TEMPLATE = """# Design Spec

iteration: {iteration}

## Requirements

- rail_volts: {rail_volts}
- regulator_capacity_floor_ma: {regulator_capacity_floor_ma}
- peripherals: {peripherals_csv}

## Notes

{notes}
"""


def _parse_spec(content: str) -> dict[str, str]:
    """Parse the spec back into a flat dict (rail_volts, etc.)."""
    out: dict[str, str] = {}
    for line in content.splitlines():
        match = re.match(r"^- ([a-z_]+): (.+)$", line.strip())
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class HardwarePlanner:
    """Reads user intent + prior gate failures, writes ``design_spec.md``.

    First iteration: pick a default regulator-capacity floor based on
    intent peripherals. Subsequent iterations: if the evaluator's
    ``power_budget`` gate failed, ratchet the floor up so the generator
    picks the beefier regulator next round.
    """

    def __init__(self, intent: HardwareUserIntent) -> None:
        self._intent = intent

    async def plan(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        prior_feedback: EvaluatorResult | None,
    ) -> PlannerResult:
        rail = self._intent.target_rail_volts or 3.3
        peripherals = self._intent.peripherals or _peripherals_from_description(
            self._intent.description
        )

        # Default floor: a generous 500 mA covers MCU + a couple of
        # sensors. If the evaluator failed power_budget last time we
        # crank to 2 A (forces the LM2596 buck regulator).
        if prior_feedback and not prior_feedback.passed:
            failed = {g.name for g in prior_feedback.gates if not g.passed}
            if "power_budget" in failed:
                floor = 2_000.0
            else:
                floor = 1_000.0
        else:
            floor = 500.0

        spec = _SPEC_TEMPLATE.format(
            iteration=iteration,
            rail_volts=rail,
            regulator_capacity_floor_ma=floor,
            peripherals_csv=",".join(peripherals) or "(none)",
            notes=f"Intent: {self._intent.description}",
        )
        await store.put(
            run_id,
            "design_spec.md",
            spec,
            metadata={"iteration": str(iteration)},
        )
        return PlannerResult(
            spec_artifact="design_spec.md",
            notes=f"floor={floor} mA, peripherals={peripherals}",
        )


def _peripherals_from_description(text: str) -> list[str]:
    """Keyword-extract peripheral keywords from the brief."""
    lowered = text.lower()
    peripherals = []
    if "wifi" in lowered:
        peripherals.append("wifi")
    if "bluetooth" in lowered or "ble" in lowered:
        peripherals.append("ble")
    if "sensor" in lowered or "temp" in lowered or "humidity" in lowered:
        peripherals.append("environmental_sensor")
    if "accel" in lowered or "motion" in lowered:
        peripherals.append("accelerometer")
    return peripherals


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class HardwareGenerator:
    """Picks MCU + regulator + sensors from the catalog, writes BOM."""

    async def generate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
    ) -> GeneratorResult:
        spec = await store.get(run_id, spec_artifact)
        if spec is None:
            raise RuntimeError(f"spec artifact {spec_artifact!r} missing")
        parsed = _parse_spec(spec.content)
        rail = float(parsed.get("rail_volts", "3.3"))
        floor = float(parsed.get("regulator_capacity_floor_ma", "500"))
        peripherals = [
            p.strip()
            for p in parsed.get("peripherals", "").split(",")
            if p.strip() and p.strip() != "(none)"
        ]

        # Pick exactly one MCU (prefer ESP32 if wifi is requested).
        if "wifi" in peripherals:
            mcu = _CATALOG_BY_MPN["ESP32-WROOM-32"]
        else:
            mcu = _CATALOG_BY_MPN["STM32H743VIT6"]

        # Pick a regulator that clears the floor — smallest one that
        # fits, so we don't over-spec.
        candidates = [
            p
            for p in _CATALOG
            if p.category == "regulator"
            and abs(p.rail_volts - rail) < 0.1
            and p.peak_current_ma >= floor
        ]
        if not candidates:
            raise RuntimeError(f"no regulator covers floor={floor} mA at rail={rail} V")
        regulator = min(candidates, key=lambda p: p.peak_current_ma)

        sensors: list[CatalogPart] = []
        if "environmental_sensor" in peripherals:
            sensors.append(_CATALOG_BY_MPN["BME280"])
        if "accelerometer" in peripherals:
            sensors.append(_CATALOG_BY_MPN["BMA400"])

        bom_parts = [mcu, regulator, *sensors]

        # Write bom.csv
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["mpn", "description", "category", "rail_v", "peak_ma", "qty"])
        for part in bom_parts:
            writer.writerow(
                [
                    part.mpn,
                    part.description,
                    part.category,
                    part.rail_volts,
                    part.peak_current_ma,
                    1,
                ]
            )
        await store.put(run_id, "bom.csv", buf.getvalue())

        # Write schematic_outline.md (text-only outline for now)
        outline_lines = [
            "# Schematic Outline",
            "",
            f"iteration: {iteration}",
            "",
            f"- Power: {regulator.mpn} → {rail} V rail",
            f"- MCU: {mcu.mpn}",
        ]
        for sensor in sensors:
            outline_lines.append(f"- Sensor: {sensor.mpn} on I2C (3.3 V)")
        await store.put(run_id, "schematic_outline.md", "\n".join(outline_lines) + "\n")

        return GeneratorResult(
            output_artifacts=["bom.csv", "schematic_outline.md"],
            notes=f"MCU={mcu.mpn}, regulator={regulator.mpn}",
        )


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class HardwareEvaluator:
    """Runs three programmatic gates on the spec + BOM."""

    async def evaluate(
        self,
        run_id: str,
        store: ArtifactStore,
        *,
        iteration: int,
        spec_artifact: str,
        output_artifacts: list[str],
    ) -> EvaluatorResult:
        spec = await store.get(run_id, spec_artifact)
        bom = await store.get(run_id, "bom.csv")
        if spec is None or bom is None:
            return EvaluatorResult(
                gates=[
                    GateResult(name="artifacts_present", passed=False, detail="missing"),
                ],
                passed=False,
            )

        parsed_spec = _parse_spec(spec.content)
        rail_required = float(parsed_spec.get("rail_volts", "3.3"))

        parts = _parse_bom(bom.content)
        mcu_parts = [p for p in parts if p["category"] == "mcu"]
        regulator_parts = [p for p in parts if p["category"] == "regulator"]

        gates: list[GateResult] = []

        # 1) bom_present
        bom_ok = bool(mcu_parts) and bool(regulator_parts)
        gates.append(
            GateResult(
                name="bom_present",
                passed=bom_ok,
                detail="OK" if bom_ok else "MCU or regulator missing",
            )
        )

        # 2) voltage_margin — regulator rail ≥ required rail (3.3V is
        # the floor; LDOs need ~0.3-1 V dropout but for an exact-match
        # rail we only check equality here.)
        if regulator_parts:
            reg = regulator_parts[0]
            v_ok = float(reg["rail_v"]) >= rail_required
            gates.append(
                GateResult(
                    name="voltage_margin",
                    passed=v_ok,
                    detail=(
                        "OK"
                        if v_ok
                        else f"regulator {reg['mpn']} {reg['rail_v']}V < required {rail_required}V"
                    ),
                )
            )
        else:
            gates.append(
                GateResult(
                    name="voltage_margin",
                    passed=False,
                    detail="no regulator to evaluate",
                )
            )

        # 3) power_budget — sum of all non-regulator peak currents ≤
        # regulator capacity. Add 20% headroom margin.
        if regulator_parts:
            reg = regulator_parts[0]
            consumers = [p for p in parts if p["category"] != "regulator"]
            total_ma = sum(float(p["peak_ma"]) for p in consumers)
            capacity = float(reg["peak_ma"])
            budget_ok = total_ma * 1.2 <= capacity
            gates.append(
                GateResult(
                    name="power_budget",
                    passed=budget_ok,
                    detail=(
                        f"consumers={total_ma:.1f} mA × 1.2 headroom, "
                        f"regulator capacity={capacity:.1f} mA"
                    ),
                    metadata={
                        "consumer_total_ma": f"{total_ma:.1f}",
                        "regulator_capacity_ma": f"{capacity:.1f}",
                    },
                )
            )
        else:
            gates.append(GateResult(name="power_budget", passed=False, detail="no regulator"))

        return EvaluatorResult(
            gates=gates,
            passed=all(g.passed for g in gates),
            notes=f"evaluated {len(parts)} BOM rows",
        )


def _parse_bom(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return [dict(row) for row in reader]
