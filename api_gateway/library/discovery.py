"""Read a printer's own controller into a proposed library entry (FORGE-451 step 3).

``library.discover_machine`` asks an OctoPrint, Moonraker (Klipper) or
PrusaLink server what the machine is set up as: its build volume or axis
travel, its nozzle, its current state. The answer is a *proposed* instance,
never a registered one: registering it is ``library.register_machine``,
held for the person like every library write.

What the controller reports is recorded as ``machine_report`` with the
endpoint and time it was read. A figure the controller does not report is
left out and named in ``missing``; nothing is filled in.

Only a few fixed read endpoints are called, and only the parsed figures are
returned, never a raw body. The credential is read from the gateway's
environment by name, and only names starting ``METAFORGE_MACHINE_`` are
accepted (``twin_core.library.machines.parse_connection``), so discovery
cannot be used to send another gateway secret to an arbitrary host.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from twin_core.library.machines import MachineLibraryError, parse_connection

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.discovery")

DEFAULT_TIMEOUT_SECONDS = 10.0

__all__ = ["DiscoveryError", "discover_machine"]


class DiscoveryError(RuntimeError):
    """The controller could not be read; the message says what to check."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _headers(connection: dict[str, Any]) -> dict[str, str]:
    env = connection.get("credential_env")
    if not env:
        return {}
    key = os.environ.get(env, "").strip()
    if not key:
        raise DiscoveryError(
            f"{env} is not set on the gateway; set it to the controller's API key, "
            "or leave credential_env out if the controller needs none"
        )
    return {"X-Api-Key": key}


async def _get(http: httpx.AsyncClient, base: str, path: str, headers: dict[str, str]) -> Any:
    try:
        response = await http.get(f"{base}{path}", headers=headers)
    except httpx.HTTPError as exc:
        raise DiscoveryError(f"could not reach {base}{path}: {exc}") from exc
    if response.status_code in (401, 403):
        raise DiscoveryError(f"{base}{path} refused the credential (HTTP {response.status_code})")
    if response.status_code == 409:
        return None  # OctoPrint: printer not operational. A state, not an error.
    if response.is_error:
        raise DiscoveryError(f"{base}{path} answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise DiscoveryError(f"{base}{path} did not answer JSON") from exc


def _cap(value: Any, unit: str, ref: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "source_kind": "machine_report", "source_ref": ref}


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def _octoprint(
    http: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    version = await _get(http, base, "/api/version", headers) or {}
    profiles = (await _get(http, base, "/api/printerprofiles", headers) or {}).get("profiles", {})
    profile = next((p for p in profiles.values() if p.get("current")), None) or next(
        iter(profiles.values()), {}
    )
    printer = await _get(http, base, "/api/printer", headers)
    flags = ((printer or {}).get("state") or {}).get("flags") or {}
    if printer is None or not flags.get("operational"):
        status = "offline"
    elif flags.get("printing") or flags.get("paused"):
        status = "busy"
    else:
        status = "available"
    ref = f"OctoPrint printer profile '{profile.get('id', '?')}' at {base}, read {_now()}"
    caps: dict[str, Any] = {}
    volume = profile.get("volume") or {}
    dims = [_positive(volume.get(k)) for k in ("width", "depth", "height")]
    if all(dims) and volume.get("formFactor", "rectangular") == "rectangular":
        caps["build_volume_mm"] = _cap(dims, "mm", ref)
    nozzle = _positive((profile.get("extruder") or {}).get("nozzleDiameter"))
    if nozzle:
        caps["nozzle_mm"] = _cap(nozzle, "mm", ref)
    observed = {
        "software": version.get("text") or version.get("server"),
        "model": profile.get("model") or profile.get("name"),
        "state": status,
    }
    return caps, {"observed": observed, "status": status, "name": profile.get("name")}


async def _moonraker(
    http: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    info = ((await _get(http, base, "/printer/info", headers)) or {}).get("result") or {}
    query = await _get(
        http, base, "/printer/objects/query?configfile=settings&print_stats=state", headers
    )
    status_obj = ((query or {}).get("result") or {}).get("status") or {}
    settings = (status_obj.get("configfile") or {}).get("settings") or {}
    printing = (status_obj.get("print_stats") or {}).get("state") in ("printing", "paused")
    if info.get("state") != "ready":
        status = "offline"
    else:
        status = "busy" if printing else "available"
    ref = f"Klipper configuration via Moonraker at {base}, read {_now()}"
    caps: dict[str, Any] = {}
    travel: list[float | None] = []
    for axis in ("x", "y", "z"):
        stepper = settings.get(f"stepper_{axis}") or {}
        top = _positive(stepper.get("position_max"))
        bottom = float(stepper.get("position_min") or 0)
        travel.append(top - bottom if top is not None and top - bottom > 0 else None)
    if all(travel):
        caps["travel_mm"] = _cap(travel, "mm", ref + " (axis travel, position_max - position_min)")
    nozzle = _positive((settings.get("extruder") or {}).get("nozzle_diameter"))
    if nozzle:
        caps["nozzle_mm"] = _cap(nozzle, "mm", ref)
    observed = {
        "software": info.get("software_version"),
        "model": (settings.get("printer") or {}).get("kinematics"),
        "state": status,
    }
    return caps, {"observed": observed, "status": status, "name": info.get("hostname")}


async def _prusalink(
    http: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    version = await _get(http, base, "/api/version", headers) or {}
    info = await _get(http, base, "/api/v1/info", headers) or {}
    state = str(
        (((await _get(http, base, "/api/v1/status", headers)) or {}).get("printer") or {}).get(
            "state", ""
        )
    ).upper()
    if state in ("PRINTING", "PAUSED", "BUSY", "ATTENTION"):
        status = "busy"
    elif state in ("IDLE", "READY", "FINISHED", "STOPPED"):
        status = "available"
    else:
        status = "offline"
    ref = f"PrusaLink at {base}, read {_now()}"
    caps: dict[str, Any] = {}
    nozzle = _positive(info.get("nozzle_diameter") or version.get("nozzle_diameter"))
    if nozzle:
        caps["nozzle_mm"] = _cap(nozzle, "mm", ref)
    observed = {
        "software": version.get("text") or version.get("server"),
        "model": None,
        "state": status,
    }
    name = info.get("hostname") or version.get("hostname")
    return caps, {"observed": observed, "status": status, "name": name}


_READERS = {"octoprint": _octoprint, "moonraker": _moonraker, "prusalink": _prusalink}

#: What a library FDM entry can hold that no controller reports.
_NEVER_REPORTED = (
    "min_wall_mm",
    "min_feature_mm",
    "tolerance_mm",
    "max_overhang_deg",
    "materials",
)


async def discover_machine(
    connection: dict[str, Any],
    *,
    name: str = "",
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read the controller and return a proposed instance. Registers nothing."""
    try:
        conn = parse_connection(connection)
    except MachineLibraryError as exc:
        raise DiscoveryError(str(exc)) from exc
    protocol = conn.get("protocol")
    reader = _READERS.get(str(protocol))
    if reader is None:
        raise DiscoveryError("discovery reads octoprint, moonraker or prusalink connections")
    base = str(conn["url"])
    headers = _headers(conn)
    with tracer.start_as_current_span("library.discover_machine") as span:
        span.set_attribute("machine.protocol", str(protocol))
        owned = client is None
        http = client or httpx.AsyncClient(timeout=timeout_seconds)
        try:
            caps, found = await reader(http, base, headers)
        except DiscoveryError as exc:
            span.record_exception(exc)
            logger.warning("machine_discovery_failed", protocol=protocol, error=str(exc))
            raise
        finally:
            if owned:
                await http.aclose()
    missing = [k for k in ("build_volume_mm", "nozzle_mm") if k not in caps]
    if "travel_mm" in caps:
        missing.remove("build_volume_mm")
    missing += list(_NEVER_REPORTED)
    proposed = {
        "layer": "instance",
        "kind": "fdm_printer",
        "name": name.strip() or str(found.get("name") or f"{protocol} printer"),
        "processes": ["fdm"],
        "route": "in_house",
        "status": found["status"],
        "capabilities": caps,
        "connection": conn,
    }
    logger.info(
        "machine_discovered", protocol=protocol, status=found["status"], capabilities=sorted(caps)
    )
    return {
        "proposed": proposed,
        "observed": found["observed"],
        "missing": missing,
        "next_step": (
            "Nothing was registered. Show the person this entry and the missing figures; "
            "add any they can source (a spec sheet, a calibration print), then call "
            "library.register_machine with it. That write is held for their approval."
        ),
    }
