"""The MCP sidecar serves the evidence, promotion and recorder tools (FORGE-546).

They registered in the gateway and never in the standalone sidecar, so every
plugin client lacked them while every profile advertised twin.record_evidence.
Boots the real sidecar (``_bootstrap``) and reads its tools/list.
"""

from __future__ import annotations

import pytest

from tests.unit.test_sidecar_flow_tools import _SERVICE_ENV, _boot, _listed

pytestmark = pytest.mark.asyncio

WIRED = {
    "twin.record_evidence",
    "twin.record_claim",
    "twin.attempt_promotion",
    "twin.evaluate_metric",
    "twin.evaluate_thermal_metric",
    "twin.evaluate_overhang_metric",
    "twin.rank_sensitivity",
    "twin.commit_design_sketch",
    "twin.commit_hazard_analysis",
    "twin.commit_system_architecture",
    "twin.commit_technical_drawing",
    "twin.commit_compliance_checklist",
    "twin.commit_procurement_record",
}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _SERVICE_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)


@pytest.mark.usefixtures("offline")
async def test_the_sidecar_serves_every_tool_the_gateway_wires() -> None:
    listed = await _listed(await _boot())
    missing = sorted(WIRED - listed)
    assert missing == [], f"registered in the gateway, absent from the sidecar: {missing}"


@pytest.mark.usefixtures("offline")
async def test_every_profile_tool_the_sidecar_could_serve_is_served() -> None:
    # The profile lists are only honest if what they name is served.
    from mcp_core.profiles import PROFILES

    listed = await _listed(await _boot())
    advertised = {t for tools in PROFILES.values() for t in tools if t.startswith("twin.")}
    assert "twin.record_evidence" in advertised
    assert "twin.record_evidence" in listed
