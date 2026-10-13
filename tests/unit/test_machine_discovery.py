"""library.discover_machine: OctoPrint, Moonraker and PrusaLink (FORGE-451 step 3)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from api_gateway.library.discovery import DiscoveryError, discover_machine
from mcp_core.annotations import OPEN_WORLD, READ_ONLY
from twin_core.library.machines import SqliteMachineLibrary

KEY_ENV = "METAFORGE_MACHINE_TEST_KEY"


def _transport(routes: dict[str, Any], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.raw_path.decode()
        if path not in routes:
            return httpx.Response(404, json={})
        body = routes[path]
        if isinstance(body, int):
            return httpx.Response(body, json={})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


OCTOPRINT = {
    "/api/version": {"api": "0.1", "server": "1.10.2", "text": "OctoPrint 1.10.2"},
    "/api/printerprofiles": {
        "profiles": {
            "_default": {"id": "_default", "name": "Default", "current": False},
            "cr10": {
                "id": "cr10",
                "name": "CR-10",
                "model": "Creality CR-10",
                "current": True,
                "volume": {"width": 300, "depth": 300, "height": 400, "formFactor": "rectangular"},
                "extruder": {"count": 1, "nozzleDiameter": 0.4},
            },
        }
    },
    "/api/printer": {"state": {"flags": {"operational": True, "printing": False}}},
}

MOONRAKER = {
    "/printer/info": {
        "result": {"state": "ready", "hostname": "voron", "software_version": "v0.12.0"}
    },
    "/printer/objects/query?configfile=settings&print_stats=state": {
        "result": {
            "status": {
                "configfile": {
                    "settings": {
                        "stepper_x": {"position_min": 0, "position_max": 250},
                        "stepper_y": {"position_min": -5, "position_max": 250},
                        "stepper_z": {"position_max": 230},
                        "extruder": {"nozzle_diameter": 0.4},
                        "printer": {"kinematics": "corexy"},
                    }
                },
                "print_stats": {"state": "printing"},
            }
        }
    },
}

PRUSALINK = {
    "/api/version": {"api": "2.0.0", "server": "2.1.2", "text": "PrusaLink", "hostname": "mk4"},
    "/api/v1/info": {"nozzle_diameter": 0.6, "hostname": "mk4"},
    "/api/v1/status": {"printer": {"state": "IDLE"}},
}


async def _discover(routes: dict[str, Any], protocol: str, **kw: Any) -> tuple[Any, list]:
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_transport(routes, seen)) as http:
        result = await discover_machine(
            {"protocol": protocol, "url": "http://printer.local/", **kw}, client=http
        )
    return result, seen


@pytest.mark.asyncio
async def test_octoprint_reads_the_current_profile_with_its_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, "k123")
    result, seen = await _discover(OCTOPRINT, "octoprint", credential_env=KEY_ENV)
    proposed = result["proposed"]
    caps = proposed["capabilities"]
    assert caps["build_volume_mm"]["value"] == [300, 300, 400]
    assert caps["build_volume_mm"]["source_kind"] == "machine_report"
    assert "OctoPrint printer profile 'cr10'" in caps["build_volume_mm"]["source_ref"]
    assert caps["nozzle_mm"]["value"] == 0.4
    assert proposed["status"] == "available"
    assert proposed["name"] == "CR-10"
    assert result["observed"]["model"] == "Creality CR-10"
    assert "min_wall_mm" in result["missing"]
    # The key went to the machine in the header, and nothing secret came back.
    assert all(r.headers["X-Api-Key"] == "k123" for r in seen)
    assert "k123" not in str(result)
    # The proposal is one the library accepts as it stands.
    SqliteMachineLibrary().register(proposed)


@pytest.mark.asyncio
async def test_moonraker_reads_axis_travel_and_a_busy_printer() -> None:
    result, seen = await _discover(MOONRAKER, "moonraker")
    caps = result["proposed"]["capabilities"]
    assert caps["travel_mm"]["value"] == [250, 255, 230]
    assert "axis travel" in caps["travel_mm"]["source_ref"]
    assert "build_volume_mm" not in caps
    assert result["proposed"]["status"] == "busy"
    assert result["proposed"]["name"] == "voron"
    assert "X-Api-Key" not in seen[0].headers
    assert "build_volume_mm" not in result["missing"]
    SqliteMachineLibrary().register(result["proposed"])


@pytest.mark.asyncio
async def test_prusalink_reports_a_nozzle_and_no_volume() -> None:
    result, _ = await _discover(PRUSALINK, "prusalink")
    caps = result["proposed"]["capabilities"]
    assert caps == {"nozzle_mm": caps["nozzle_mm"]}
    assert caps["nozzle_mm"]["value"] == 0.6
    assert "build_volume_mm" in result["missing"]
    assert result["proposed"]["status"] == "available"


@pytest.mark.asyncio
async def test_an_octoprint_printer_that_is_not_operational_reads_offline() -> None:
    routes = {**OCTOPRINT, "/api/printer": 409}
    result, _ = await _discover(routes, "octoprint")
    assert result["proposed"]["status"] == "offline"


@pytest.mark.asyncio
async def test_a_refused_key_and_an_unset_key_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(DiscoveryError, match="refused the credential"):
        await _discover({**OCTOPRINT, "/api/version": 403}, "octoprint")
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(DiscoveryError, match="not set on the gateway"):
        await _discover(OCTOPRINT, "octoprint", credential_env=KEY_ENV)


@pytest.mark.asyncio
async def test_no_other_gateway_secret_can_be_named() -> None:
    with pytest.raises(DiscoveryError, match="METAFORGE_MACHINE_"):
        await _discover(OCTOPRINT, "octoprint", credential_env="OPENROUTER_API_KEY")
    with pytest.raises(DiscoveryError, match="octoprint, moonraker or prusalink"):
        await discover_machine({"protocol": "none"})


def test_discovery_is_a_read_that_reaches_outside() -> None:
    assert "library.discover_machine" in READ_ONLY
    assert "library.discover_machine" in OPEN_WORLD
