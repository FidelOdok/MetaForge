"""Hardware-design three-agent triple (MET-474).

A concrete ``Planner`` / ``Generator`` / ``Evaluator`` that runs on
top of the shared harness in ``orchestrator.harness.three_agent``. The
agents here are deterministic — they hit the local component catalog
+ programmatic constraint checks rather than an LLM. The LLM-driven
production variant slots in as a drop-in replacement once an
``LLMProvider`` is wired into the orchestrator (MET-462 plumbing is
the same path the knowledge property extractor uses).

The triple is opinionated about the IoT design scenario the MET-474
acceptance criteria call out, but the entry points are generic enough
to handle any "spec → BOM → constraint-validate" flow. Other
verticals (industrial / drone / wearable) plug in by extending the
component catalog.
"""

from orchestrator.harness.hardware.agents import (
    HardwareEvaluator,
    HardwareGenerator,
    HardwarePlanner,
    HardwareUserIntent,
)

__all__ = [
    "HardwareEvaluator",
    "HardwareGenerator",
    "HardwarePlanner",
    "HardwareUserIntent",
]
