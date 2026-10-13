"""The machine library: what can make a part, as data (FORGE-451, thin slice).

Process ranking and the DFM review (FORGE-611) need a machine's limits: its
build volume, the smallest feature it holds, the materials it runs. Until now
those were typed into a run as free text and re-sourced every time. Here a
machine is recorded once and read by every run.

Two layers, so a catalogue entry and the one on your bench stay apart:

* a **model** is the catalogue spec ("Creality CR-10"), each value from the
  manufacturer's spec sheet;
* an **instance** is a particular machine ("the CR-10 in the workshop"),
  usually pointing at a model, overriding what differs (a 0.6 mm nozzle, a
  measured accuracy) and carrying its own status and route.

Every capability carries its unit and its source, and the source is checked:
no value without one, and an ``assumption`` is marked for review. Nothing is
defaulted. The library is cross-project data, so it lives outside any one
project's twin; a run reads it through ``machine_limits``.

Layer: stdlib and structlog (twin_core may import nothing above it).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "CAPABILITIES",
    "CAPABILITY_SOURCE_KINDS",
    "CONNECTION_PROTOCOLS",
    "CREDENTIAL_ENV_PREFIX",
    "MACHINE_KINDS",
    "MACHINE_STATUSES",
    "Capability",
    "MachineInstance",
    "MachineLibraryError",
    "MachineModel",
    "MachineNotFoundError",
    "SqliteMachineLibrary",
    "default_machine_library_path",
    "effective_capabilities",
    "machine_limits",
    "parse_capabilities",
    "parse_connection",
    "summarize_limits",
]

#: What kind of machine it is. ``other`` is allowed; its processes say more.
MACHINE_KINDS: tuple[str, ...] = (
    "fdm_printer",
    "resin_printer",
    "sls_printer",
    "cnc_mill",
    "cnc_router",
    "cnc_lathe",
    "laser_cutter",
    "plasma_cutter",
    "waterjet",
    "press_brake",
    "welder",
    "other",
)

MACHINE_STATUSES: tuple[str, ...] = ("available", "busy", "offline", "retired")

#: Where a capability value came from, strongest first. ``assumption`` is
#: allowed but is never mistaken for a sourced value: it is marked for review.
CAPABILITY_SOURCE_KINDS: tuple[str, ...] = (
    "measurement",
    "manufacturer_spec",
    # FORGE-451: read from the machine's own controller (an OctoPrint
    # printer profile, a Klipper config). What the machine is set up as,
    # with the endpoint and date it was read as the reference.
    "machine_report",
    "user",
    "assumption",
)
_SOURCE_NEEDS_REF = ("measurement", "manufacturer_spec", "machine_report")

#: Protocols a machine connection may name (discovery and, later, dispatch).
CONNECTION_PROTOCOLS: tuple[str, ...] = ("octoprint", "moonraker", "prusalink", "none")
#: A credential is named, never stored, and only from this namespace: the
#: gateway sends it to the machine's URL, so a free name would let anyone who
#: can register a machine send any of the gateway's secrets to a host of their
#: choosing.
CREDENTIAL_ENV_PREFIX = "METAFORGE_MACHINE_"
_SECRET_KEYS = ("api_key", "apikey", "key", "token", "password", "secret", "auth")


def parse_connection(raw: Any) -> dict[str, Any]:
    """A connection: protocol, URL and the credential's env name, nothing secret."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise MachineLibraryError("connection must be an object")
    if any(k.lower() in _SECRET_KEYS for k in raw):
        raise MachineLibraryError(
            "connection must name the environment variable holding a secret "
            "(credential_env), never the secret itself"
        )
    unknown = set(raw) - {"protocol", "url", "credential_env"}
    if unknown:
        raise MachineLibraryError(f"connection has no field {', '.join(sorted(unknown))}")
    protocol = str(raw.get("protocol") or "none").strip().lower()
    if protocol not in CONNECTION_PROTOCOLS:
        raise MachineLibraryError(
            f"connection.protocol must be one of {', '.join(CONNECTION_PROTOCOLS)}"
        )
    url = str(raw.get("url") or "").strip()
    if protocol != "none" and not url.startswith(("http://", "https://")):
        raise MachineLibraryError("connection.url must be an http:// or https:// URL")
    env = str(raw.get("credential_env") or "").strip()
    if env and (
        not env.startswith(CREDENTIAL_ENV_PREFIX)
        or not env.replace("_", "").isalnum()
        or env.upper() != env
    ):
        raise MachineLibraryError(
            f"connection.credential_env must be an upper-case name starting "
            f"{CREDENTIAL_ENV_PREFIX}, e.g. {CREDENTIAL_ENV_PREFIX}CR10_KEY"
        )
    out: dict[str, Any] = {"protocol": protocol}
    if url:
        out["url"] = url.rstrip("/")
    if env:
        out["credential_env"] = env
    return out


@dataclass(frozen=True)
class _Spec:
    unit: str
    shape: str  # number | vector3 | list
    description: str


#: The capabilities the library knows. A key outside this table is refused:
#: a misspelt key would otherwise sit there unread by every check.
CAPABILITIES: dict[str, _Spec] = {
    "build_volume_mm": _Spec("mm", "vector3", "largest part it makes in one piece, X x Y x Z"),
    "travel_mm": _Spec("mm", "vector3", "axis travel (CNC, laser), X x Y x Z"),
    "nozzle_mm": _Spec("mm", "number", "nozzle diameter (FDM)"),
    "layer_height_min_mm": _Spec("mm", "number", "thinnest layer"),
    "min_feature_mm": _Spec("mm", "number", "smallest feature it reproduces"),
    "min_wall_mm": _Spec("mm", "number", "thinnest wall it makes reliably"),
    "tolerance_mm": _Spec("mm", "number", "achievable dimensional tolerance (+/-)"),
    "max_overhang_deg": _Spec("deg", "number", "steepest unsupported overhang from vertical"),
    "min_bend_radius_mm": _Spec("mm", "number", "smallest inside bend radius"),
    "min_tool_diameter_mm": _Spec("mm", "number", "smallest cutter, sets inside corner radii"),
    "max_sheet_thickness_mm": _Spec("mm", "number", "thickest sheet it cuts or bends"),
    "max_part_mass_kg": _Spec("kg", "number", "heaviest part it handles"),
    "spindle_max_rpm": _Spec("rpm", "number", "spindle speed"),
    "laser_power_w": _Spec("W", "number", "laser output power"),
    "materials": _Spec("", "list", "materials it runs, e.g. PLA, PETG, 6061 aluminium"),
}


class MachineLibraryError(ValueError):
    """A machine or capability that does not pass the library's rules."""


class MachineNotFoundError(KeyError):
    """No machine with that id."""


@dataclass(frozen=True)
class Capability:
    value: Any
    unit: str
    source_kind: str
    source_ref: str = ""
    needs_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(key: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise MachineLibraryError(f"capability {key} must be a positive number")
    return float(value)


def _parse_one(key: str, raw: Any) -> Capability:
    spec = CAPABILITIES.get(key)
    if spec is None:
        raise MachineLibraryError(
            f"unknown capability '{key}'; known: {', '.join(sorted(CAPABILITIES))}"
        )
    if not isinstance(raw, dict):
        raise MachineLibraryError(f"capability {key} must be an object with value and source")
    source = raw.get("source") if isinstance(raw.get("source"), dict) else raw
    kind = str(source.get("source_kind") or source.get("kind") or "").strip()
    ref = str(source.get("source_ref") or source.get("ref") or "").strip()
    if kind not in CAPABILITY_SOURCE_KINDS:
        raise MachineLibraryError(
            f"capability {key} needs a source kind, one of {', '.join(CAPABILITY_SOURCE_KINDS)}"
        )
    if kind in _SOURCE_NEEDS_REF and not ref:
        raise MachineLibraryError(f"capability {key}: a {kind} source needs its reference")
    unit = str(raw.get("unit") or spec.unit)
    # A list of names has no unit; "list" or nothing both say so.
    if spec.shape != "list" and unit != spec.unit:
        raise MachineLibraryError(f"capability {key} is in {spec.unit}, not {unit}")
    value = raw.get("value")
    if spec.shape == "number":
        parsed: Any = _number(key, value)
    elif spec.shape == "vector3":
        if not isinstance(value, list | tuple) or len(value) != 3:
            raise MachineLibraryError(f"capability {key} must be three numbers, X x Y x Z")
        parsed = [_number(key, v) for v in value]
    else:
        if not isinstance(value, list | tuple) or not all(
            isinstance(v, str) and v.strip() for v in value
        ):
            raise MachineLibraryError(f"capability {key} must be a list of names")
        parsed = [v.strip() for v in value]
    return Capability(
        value=parsed,
        unit=spec.unit,
        source_kind=kind,
        source_ref=ref,
        needs_review=kind == "assumption",
    )


def parse_capabilities(raw: Any) -> dict[str, Capability]:
    """``{key: {value, source_kind, source_ref}}`` checked; raises saying why."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise MachineLibraryError("capabilities must be an object keyed by capability name")
    return {key: _parse_one(key, value) for key, value in raw.items()}


def _caps_from_json(data: dict[str, Any]) -> dict[str, Capability]:
    return {k: Capability(**v) for k, v in (data or {}).items()}


@dataclass
class MachineModel:
    """A catalogue entry: the make and model, with its spec-sheet values."""

    id: str
    kind: str
    make: str
    model: str
    processes: list[str] = field(default_factory=list)
    capabilities: dict[str, Capability] = field(default_factory=dict)
    version: int = 1
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer": "model",
            "kind": self.kind,
            "make": self.make,
            "model": self.model,
            "processes": list(self.processes),
            "capabilities": {k: c.as_dict() for k, c in self.capabilities.items()},
            "version": self.version,
            "updated_at": self.updated_at,
        }


@dataclass
class MachineInstance:
    """One real machine: its model, what differs, where it is and whether it is free."""

    id: str
    name: str
    kind: str
    model_id: str | None = None
    processes: list[str] = field(default_factory=list)
    route: str = "in_house"
    location: str = ""
    status: str = "available"
    capabilities: dict[str, Capability] = field(default_factory=dict)
    #: Steps 3 and 4: how to reach it (``{"type": "octoprint", "base_url": ...,
    #: "credential_env": "NAME"}``). A secret is never stored, only the name of
    #: the environment variable that holds it.
    connection: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer": "instance",
            "name": self.name,
            "kind": self.kind,
            "model_id": self.model_id,
            "processes": list(self.processes),
            "route": self.route,
            "location": self.location,
            "status": self.status,
            "capabilities": {k: c.as_dict() for k, c in self.capabilities.items()},
            "connection": dict(self.connection),
            "version": self.version,
            "updated_at": self.updated_at,
        }


def effective_capabilities(
    instance: MachineInstance, model: MachineModel | None
) -> dict[str, Capability]:
    """The model's values with the instance's own values on top."""
    merged = dict(model.capabilities) if model else {}
    merged.update(instance.capabilities)
    return merged


def _value(caps: dict[str, Capability], key: str) -> Any:
    cap = caps.get(key)
    return cap.value if cap else None


def _ref(caps: dict[str, Capability], key: str, machine: str) -> str:
    cap = caps[key]
    where = f"{cap.source_kind}: {cap.source_ref}" if cap.source_ref else cap.source_kind
    return f"{machine} {key} ({where})"


def machine_limits(instance: MachineInstance, model: MachineModel | None) -> dict[str, Any]:
    """A machine's limits in the shapes FORGE-611 consumes.

    ``candidate`` is a ``rank_manufacturing_processes`` candidate; ``limits``
    are ``check_manufacturability`` process limits, each with its source.
    Only capabilities the machine actually has a value for appear: a missing
    one stays missing, so the check reads it as unknown.
    """
    caps = effective_capabilities(instance, model)
    processes = instance.processes or (model.processes if model else [])
    label = instance.name
    envelope_key = "build_volume_mm" if "build_volume_mm" in caps else "travel_mm"
    envelope = _value(caps, envelope_key)
    candidate: dict[str, Any] = {
        "process": f"{', '.join(processes) or instance.kind} on {label}",
        "route": instance.route,
        "available": instance.status == "available",
        "max_envelope_mm": envelope,
        "min_feature_mm": _value(caps, "min_feature_mm"),
        "achievable_tolerance_mm": _value(caps, "tolerance_mm"),
        "materials": _value(caps, "materials") or [],
        "source_refs": [_ref(caps, k, label) for k in sorted(caps)],
    }
    limits: list[dict[str, Any]] = []
    if envelope is not None:
        limits.append(
            {
                "kind": "envelope",
                "max_dims_mm": envelope,
                "source_ref": _ref(caps, envelope_key, label),
            }
        )
    for key, kind, bound, scale in (
        ("min_wall_mm", "wall_thickness", "min", 1.0),
        ("min_feature_mm", "feature_size", "min", 1.0),
        ("max_overhang_deg", "overhang_angle", "max", 1.0),
        ("min_bend_radius_mm", "bend_radius", "min", 1.0),
        # A round cutter cannot cut an inside corner tighter than its radius.
        ("min_tool_diameter_mm", "inside_corner_radius", "min", 0.5),
    ):
        value = _value(caps, key)
        if value is not None:
            limits.append(
                {"kind": kind, bound: value * scale, "source_ref": _ref(caps, key, label)}
            )
    needs_review = sorted(k for k, c in caps.items() if c.needs_review)
    return {
        "machine_id": instance.id,
        "machine": label,
        "kind": instance.kind,
        "processes": list(processes),
        "candidate": candidate,
        "limits": limits,
        "needs_review": needs_review,
    }


def summarize_limits(limits: dict[str, Any]) -> str:
    """One line for a flow's context: who the machine is and its key limits.

    Built from :func:`machine_limits` output so the line and the tool agree.
    Only limits with a figure appear; assumptions are named for review.
    """
    cand = limits.get("candidate") or {}
    head = (
        f"{limits.get('machine')} [{limits.get('machine_id')}] {limits.get('kind')}, "
        f"{', '.join(limits.get('processes') or []) or 'no process stated'}, "
        f"{cand.get('route')}, {'available' if cand.get('available') else 'not available'}"
    )
    parts: list[str] = []
    for lim in limits.get("limits") or []:
        kind = lim.get("kind")
        if kind == "envelope":
            dims = "x".join(f"{float(v):g}" for v in lim.get("max_dims_mm") or [])
            parts.append(f"envelope {dims} mm")
        elif "min" in lim:
            parts.append(f"{kind} >= {float(lim['min']):g}")
        elif "max" in lim:
            parts.append(f"{kind} <= {float(lim['max']):g}")
    if cand.get("achievable_tolerance_mm") is not None:
        parts.append(f"tolerance {float(cand['achievable_tolerance_mm']):g} mm")
    if cand.get("materials"):
        parts.append(f"materials {', '.join(cand['materials'])}")
    review = limits.get("needs_review") or []
    if review:
        parts.append(f"assumed, needs review: {', '.join(review)}")
    return f"{head}: {'; '.join(parts) or 'no limits recorded'}"


def default_machine_library_path() -> Path:
    """``METAFORGE_MACHINE_LIBRARY_PATH``, else ``~/.metaforge/machine_library.db``."""
    override = os.environ.get("METAFORGE_MACHINE_LIBRARY_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".metaforge" / "machine_library.db"


def _text(value: Any, name: str, *, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise MachineLibraryError(f"{name} is required")
    return text


class SqliteMachineLibrary:
    """Models and instances in SQLite, every change kept as a version."""

    def __init__(self, path: str = ":memory:", *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS machines (
                id TEXT PRIMARY KEY, layer TEXT NOT NULL, body TEXT NOT NULL,
                version INTEGER NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS machine_history (
                id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                changed_at REAL NOT NULL, changed_by TEXT NOT NULL,
                PRIMARY KEY (id, version)
            );
            """
        )
        self._conn.commit()
        logger.info("machine_library_opened", path=path)

    # ── reads ────────────────────────────────────────────────────────────

    def _row(self, machine_id: str) -> tuple[str, dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT layer, body FROM machines WHERE id = ?", (machine_id,)
            ).fetchone()
        if row is None:
            raise MachineNotFoundError(machine_id)
        return row[0], json.loads(row[1])

    def get(self, machine_id: str) -> MachineModel | MachineInstance:
        layer, body = self._row(machine_id)
        body.pop("layer", None)
        caps = _caps_from_json(body.pop("capabilities", {}))
        if layer == "model":
            return MachineModel(capabilities=caps, **body)
        return MachineInstance(capabilities=caps, **body)

    def list(self, *, layer: str | None = None, kind: str | None = None) -> list[Any]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM machines ORDER BY updated_at").fetchall()
        out = [self.get(r[0]) for r in rows]
        if layer:
            out = [m for m in out if (layer == "model") == isinstance(m, MachineModel)]
        if kind:
            out = [m for m in out if m.kind == kind]
        return out

    def history(self, machine_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, changed_at, changed_by FROM machine_history "
                "WHERE id = ? ORDER BY version",
                (machine_id,),
            ).fetchall()
        return [{"version": r[0], "changed_at": r[1], "changed_by": r[2]} for r in rows]

    def limits(self, instance_id: str) -> dict[str, Any]:
        machine = self.get(instance_id)
        if not isinstance(machine, MachineInstance):
            raise MachineLibraryError(
                f"{instance_id} is a model; limits come from a machine instance"
            )
        model = None
        if machine.model_id:
            found = self.get(machine.model_id)
            model = found if isinstance(found, MachineModel) else None
        return machine_limits(machine, model)

    # ── writes ───────────────────────────────────────────────────────────

    def _save(self, machine: MachineModel | MachineInstance, by: str) -> None:
        body = machine.as_dict()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO machines (id, layer, body, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (machine.id, body["layer"], json.dumps(body), machine.version, machine.updated_at),
            )
            self._conn.execute(
                "INSERT INTO machine_history (id, version, body, changed_at, changed_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    machine.id,
                    machine.version,
                    json.dumps(body),
                    machine.updated_at,
                    by or "unknown",
                ),
            )
            self._conn.commit()
        logger.info(
            "machine_library_saved",
            machine_id=machine.id,
            layer=body["layer"],
            version=machine.version,
            by=by,
        )

    def register(self, data: dict[str, Any], *, by: str = "") -> MachineModel | MachineInstance:
        """Add a model (``layer: model``) or an instance (``layer: instance``)."""
        layer = _text(data.get("layer"), "layer")
        kind = _text(data.get("kind"), "kind")
        if kind not in MACHINE_KINDS:
            raise MachineLibraryError(f"kind must be one of {', '.join(MACHINE_KINDS)}")
        caps = parse_capabilities(data.get("capabilities"))
        processes = [str(p).strip() for p in data.get("processes") or [] if str(p).strip()]
        now = self._clock()
        machine: MachineModel | MachineInstance
        if layer == "model":
            machine = MachineModel(
                id=f"mm_{uuid.uuid4().hex[:12]}",
                kind=kind,
                make=_text(data.get("make"), "make"),
                model=_text(data.get("model"), "model"),
                processes=processes,
                capabilities=caps,
                updated_at=now,
            )
        elif layer == "instance":
            model_id = _text(data.get("model_id"), "model_id", required=False) or None
            if model_id is not None and not isinstance(self.get(model_id), MachineModel):
                raise MachineLibraryError(f"model_id {model_id} is not a machine model")
            route = _text(data.get("route") or "in_house", "route")
            if route not in ("in_house", "vendor"):
                raise MachineLibraryError("route must be in_house or vendor")
            status = _text(data.get("status") or "available", "status")
            if status not in MACHINE_STATUSES:
                raise MachineLibraryError(f"status must be one of {', '.join(MACHINE_STATUSES)}")
            connection = parse_connection(data.get("connection"))
            machine = MachineInstance(
                id=f"mi_{uuid.uuid4().hex[:12]}",
                name=_text(data.get("name"), "name"),
                kind=kind,
                model_id=model_id,
                processes=processes,
                route=route,
                location=_text(data.get("location"), "location", required=False),
                status=status,
                capabilities=caps,
                connection=dict(connection),
                updated_at=now,
            )
        else:
            raise MachineLibraryError("layer must be 'model' or 'instance'")
        self._save(machine, by)
        return machine

    def update(
        self, machine_id: str, changes: dict[str, Any], *, by: str = ""
    ) -> MachineModel | MachineInstance:
        """Change fields or capabilities; a new version is kept, the old one stays."""
        machine = self.get(machine_id)
        allowed = {"processes", "capabilities", "status", "location", "name", "connection", "route"}
        unknown = set(changes) - allowed
        if unknown:
            raise MachineLibraryError(f"cannot change {', '.join(sorted(unknown))}")
        if "capabilities" in changes:
            new_caps = parse_capabilities(changes["capabilities"])
            machine.capabilities = {**machine.capabilities, **new_caps}
        if "processes" in changes:
            machine.processes = [
                str(p).strip() for p in changes["processes"] or [] if str(p).strip()
            ]
        if isinstance(machine, MachineInstance):
            if "status" in changes:
                if changes["status"] not in MACHINE_STATUSES:
                    raise MachineLibraryError(
                        f"status must be one of {', '.join(MACHINE_STATUSES)}"
                    )
                machine.status = changes["status"]
            if "route" in changes:
                if changes["route"] not in ("in_house", "vendor"):
                    raise MachineLibraryError("route must be in_house or vendor")
                machine.route = changes["route"]
            if "location" in changes:
                machine.location = _text(changes["location"], "location", required=False)
            if "name" in changes:
                machine.name = _text(changes["name"], "name")
            if "connection" in changes:
                machine.connection = parse_connection(changes["connection"])
        elif set(changes) & {"status", "route", "location", "name", "connection"}:
            raise MachineLibraryError("a model has no status, route, location, name or connection")
        machine.version += 1
        machine.updated_at = self._clock()
        self._save(machine, by)
        return machine
