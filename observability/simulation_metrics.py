"""Simulation performance tracking metrics and collector for MetaForge.

Defines metric descriptors for FEA/CFD/thermal simulation runs and provides
a ``SimulationCollector`` that wraps the platform ``MetricsCollector``
pattern.  All recording methods are safe no-ops when the OTel meter is
``None``.

MET-124
"""

from __future__ import annotations

from typing import Any

from observability.metrics import MetricDefinition

# ---------------------------------------------------------------------------
# Simulation metric definitions
# ---------------------------------------------------------------------------


class SimulationMetrics:
    """Registry of simulation-specific Prometheus metrics."""

    SIMULATION_DURATION = MetricDefinition(
        name="metaforge_simulation_duration_seconds",
        type="histogram",
        description="Simulation execution duration in seconds",
        labels=["simulation_type", "solver"],
        unit="s",
        buckets=[0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600],
    )

    SIMULATION_TOTAL = MetricDefinition(
        name="metaforge_simulation_total",
        type="counter",
        description="Total simulation runs",
        labels=["simulation_type", "status"],
    )

    SIMULATION_RESOURCE_CPU = MetricDefinition(
        name="metaforge_simulation_resource_cpu_seconds",
        type="counter",
        description="Total CPU seconds consumed by simulations",
        labels=["simulation_type"],
        unit="s",
    )

    SIMULATION_RESOURCE_MEMORY = MetricDefinition(
        name="metaforge_simulation_resource_memory_bytes",
        type="gauge",
        description="Current memory usage of simulations in bytes",
        labels=["simulation_type"],
        unit="By",
    )

    SIMULATION_ACCURACY = MetricDefinition(
        name="metaforge_simulation_accuracy_score",
        type="gauge",
        description="Simulation accuracy score (0-1)",
        labels=["simulation_type", "model_version"],
    )

    @classmethod
    def all_metrics(cls) -> list[MetricDefinition]:
        """Return every simulation metric definition."""
        return [
            cls.SIMULATION_DURATION,
            cls.SIMULATION_TOTAL,
            cls.SIMULATION_RESOURCE_CPU,
            cls.SIMULATION_RESOURCE_MEMORY,
            cls.SIMULATION_ACCURACY,
        ]


# ---------------------------------------------------------------------------
# Simulation collector
# ---------------------------------------------------------------------------


class SimulationCollector:
    """Collects simulation metrics using an OTel meter (or no-op).

    Follows the same safe-no-op pattern as ``MetricsCollector``.
    """

    def __init__(self, meter: Any | None = None) -> None:
        self._meter = meter
        self._instruments: dict[str, Any] = {}

    def create_instruments(self) -> None:
        """Create OTel instruments for all simulation metrics.  No-op if
        no meter is available."""
        if self._meter is None:
            return

        for defn in SimulationMetrics.all_metrics():
            if defn.type == "counter":
                self._instruments[defn.name] = self._meter.create_counter(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )
            elif defn.type == "histogram":
                self._instruments[defn.name] = self._meter.create_histogram(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )
            elif defn.type == "gauge":
                self._instruments[defn.name] = (
                    self._meter.create_up_down_counter(
                        name=defn.name,
                        description=defn.description,
                        unit=defn.unit,
                    )
                )

    # ── Recording helpers ─────────────────────────────────────────────

    def record_simulation(
        self,
        simulation_type: str,
        solver: str,
        status: str,
        duration: float,
    ) -> None:
        """Record a simulation run (duration histogram + total counter)."""
        hist = self._instruments.get(
            SimulationMetrics.SIMULATION_DURATION.name
        )
        if hist is not None:
            hist.record(
                duration,
                attributes={
                    "simulation_type": simulation_type,
                    "solver": solver,
                },
            )

        counter = self._instruments.get(
            SimulationMetrics.SIMULATION_TOTAL.name
        )
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "simulation_type": simulation_type,
                    "status": status,
                },
            )

    def record_resource_usage(
        self,
        simulation_type: str,
        cpu_seconds: float,
        memory_bytes: int,
    ) -> None:
        """Record CPU and memory consumption for a simulation."""
        cpu_counter = self._instruments.get(
            SimulationMetrics.SIMULATION_RESOURCE_CPU.name
        )
        if cpu_counter is not None:
            cpu_counter.add(
                cpu_seconds,
                attributes={"simulation_type": simulation_type},
            )

        mem_gauge = self._instruments.get(
            SimulationMetrics.SIMULATION_RESOURCE_MEMORY.name
        )
        if mem_gauge is not None:
            mem_gauge.add(
                memory_bytes,
                attributes={"simulation_type": simulation_type},
            )

    def record_accuracy(
        self,
        simulation_type: str,
        model_version: str,
        score: float,
    ) -> None:
        """Record a simulation accuracy score."""
        gauge = self._instruments.get(
            SimulationMetrics.SIMULATION_ACCURACY.name
        )
        if gauge is not None:
            gauge.add(
                score,
                attributes={
                    "simulation_type": simulation_type,
                    "model_version": model_version,
                },
            )
