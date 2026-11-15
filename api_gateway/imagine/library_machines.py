"""The studio's machines, from the machine library (FORGE-879, 2026-11-15).

The formgraph studio read machines only from its own context, a vendored
``machines.default.json`` (a Bambu P1S-class ``fdm-generic``) and a UK supplier list. The
MetaForge machine library, where the owner's machines and the manufacturing services live,
was invisible to it: ``process.machine`` naming the owner's CR-10 V3 instance was refused,
and the print was screened with P1S figures.

On each studio write:

- When ``process.machine`` names a machine in the library (an instance, or a model), it is
  built into ``context.machines`` from the library: ``buildVolume`` and the limits the
  library holds (the instance's own values over its model's), with ``source`` naming the
  library entry and ``derived_from: "library"``. A figure the library does not hold comes
  from the studio's default machine for that process and is listed in ``defaulted``, and the
  write says which, so a default is never mistaken for the machine's own value.
- The owner's in-house machines for the studio's process are added beside it, so the
  machine can be compared and chosen.
- A machine of another process is not used, and the write says so; an id the library does
  not hold is left for the formgraph validator to report.
- Machines derived earlier are rebuilt every write; machines the agent wrote are kept.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol

import structlog

from observability.tracing import get_tracer
from twin_core.library.machines import (
    MachineInstance,
    MachineModel,
    effective_capabilities,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.library_machines")

MARK = "library"

#: A library process or machine kind, to the studio's process (its ``ruleBase``).
PROCESS_OF: dict[str, str] = {
    "fdm": "fdm",
    "fdm_printing": "fdm",
    "fdm_printer": "fdm",
    "sls": "sls",
    "sls_printing": "sls",
    "sls_printer": "sls",
    "mjf": "sls",
    "multi_jet_fusion": "sls",
    "cnc": "cnc",
    "cnc_milling": "cnc",
    "cnc_turning": "cnc",
    "cnc_mill": "cnc",
    "cnc_router": "cnc",
    "cnc_lathe": "cnc",
    "im": "im",
    "injection_moulding": "im",
    "injection_molding": "im",
}

#: Library capability -> the studio's limit key.
LIMIT_OF: dict[str, str] = {
    "min_wall_mm": "wallMin",
    "min_feature_mm": "featureMin",
    "tolerance_mm": "tolerance",
}


class MachineSource(Protocol):
    def get(self, machine_id: str) -> MachineModel | MachineInstance: ...

    def list(self, *, layer: str | None = None, kind: str | None = None) -> list[Any]: ...


@lru_cache(maxsize=1)
def _defaults() -> dict[str, dict[str, Any]]:
    """The studio's default machine per process, from its vendored defaults."""
    from api_gateway.imagine.formgraph import fg_context

    with open(fg_context.DEFAULT_MACHINES, encoding="utf-8") as fh:
        data = json.load(fh)
    out: dict[str, dict[str, Any]] = {}
    for m in data.get("machines") or []:
        process = m.get("ruleBase") or m.get("process")
        if process and process not in out:
            out[process] = m
    return out


def _process(machine: MachineModel | MachineInstance, model: MachineModel | None) -> str | None:
    names = list(machine.processes) or (list(model.processes) if model else [])
    for name in [*names, machine.kind]:
        found = PROCESS_OF.get(str(name).lower())
        if found:
            return found
    return None


def _model_of(lib: MachineSource, machine: MachineModel | MachineInstance) -> MachineModel | None:
    if isinstance(machine, MachineInstance) and machine.model_id:
        try:
            found = lib.get(machine.model_id)
        except Exception:  # noqa: BLE001 - a missing model leaves the instance's own values
            return None
        return found if isinstance(found, MachineModel) else None
    return None


#: A library process limit (its ``kind`` and bound) -> the studio's limit key.
PROCESS_LIMIT_OF: dict[tuple[str, str], str] = {
    ("wall_thickness", "min"): "wallMin",
    ("feature_size", "min"): "featureMin",
    ("hole_diameter", "min"): "holeMin",
}

#: (studio process) -> the library process's limits (``process_limits`` shape), or None.
ProcessLimits = Callable[[str], "dict[str, Any] | None"]


def _from_process(limits_doc: dict[str, Any] | None) -> tuple[dict[str, float], list[str]]:
    """The studio limits a library process gives, and the sources they came from."""
    if not isinstance(limits_doc, dict):
        return {}, []
    found: dict[str, float] = {}
    refs: list[str] = []
    for rule in limits_doc.get("limits") or []:
        if not isinstance(rule, dict):
            continue
        for bound in ("min", "max"):
            key = PROCESS_LIMIT_OF.get((str(rule.get("kind")), bound))
            value = rule.get(bound)
            if key and isinstance(value, (int, float)) and not isinstance(value, bool):
                found[key] = float(value)
                ref = str(rule.get("source_ref") or "").strip()
                if ref and ref not in refs:
                    refs.append(ref)
    raw = limits_doc.get("candidate")
    cand: dict[str, Any] = raw if isinstance(raw, dict) else {}
    tol = cand.get("achievable_tolerance_mm")
    if isinstance(tol, (int, float)) and not isinstance(tol, bool) and tol > 0:
        found["tolerance"] = float(tol)
        for ref in cand.get("source_refs") or []:
            if isinstance(ref, str) and ref not in refs:
                refs.append(ref)
    return found, refs


def studio_machine(
    machine: MachineModel | MachineInstance,
    model: MachineModel | None,
    process: str,
    process_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The formgraph machine for a library machine.

    Each limit is, best first: the machine's own figure from the library, the library
    process's sourced design-guide figure (``fromProcess``), the studio catalogue's default
    (``defaulted``).
    """
    caps = (
        effective_capabilities(machine, model)
        if isinstance(machine, MachineInstance)
        else dict(machine.capabilities)
    )
    default = _defaults().get(process, {})
    defaulted: list[str] = []
    name = (
        machine.name
        if isinstance(machine, MachineInstance)
        else f"{machine.make} {machine.model}".strip()
    )
    volume = caps.get("build_volume_mm") or caps.get("travel_mm")
    build = (
        [float(x) for x in volume.value]
        if volume is not None and isinstance(volume.value, list) and len(volume.value) == 3
        else None
    )
    if build is None and default.get("buildVolume"):
        build = list(default["buildVolume"])
        defaulted.append("buildVolume")
    limits = dict(default.get("limits") or {})
    refs: list[str] = []
    own: set[str] = set()
    for key, limit in LIMIT_OF.items():
        cap = caps.get(key)
        if cap is not None and isinstance(cap.value, (int, float)):
            limits[limit] = float(cap.value)
            own.add(limit)
            refs.append(f"{key} ({cap.source_kind}: {cap.source_ref or 'no ref'})")
    from_process, process_refs = _from_process(process_limits)
    used_process: list[str] = []
    for limit, value in from_process.items():
        if limit not in own:
            limits[limit] = value
            used_process.append(limit)
    for limit in ("wallMin", "featureMin", "holeMin", "tolerance"):
        if limit in limits and limit not in own and limit not in used_process:
            defaulted.append(limit)
    mats = caps.get("materials")
    materials = (
        [str(x).strip().lower() for x in mats.value if str(x).strip()]
        if mats is not None and isinstance(mats.value, list)
        else []
    )
    if not materials:
        materials = list(default.get("materials") or [])
        defaulted.append("materials")
    if volume is not None:
        refs.insert(0, f"build volume ({volume.source_kind}: {volume.source_ref or 'no ref'})")
    route = machine.route if isinstance(machine, MachineInstance) else "catalogue model"
    out: dict[str, Any] = {
        "id": machine.id,
        "name": name,
        "process": process,
        "ruleBase": process,
        "source": f"library {machine.id} v{machine.version} ({route})",
        "derived_from": MARK,
        "materials": materials,
        "limits": limits,
        "finishes": list(default.get("finishes") or []),
        "defaulted": sorted(set(defaulted)),
        "fromProcess": sorted(used_process),
        "note": (
            f"From the MetaForge machine library: {'; '.join(refs) or 'no sourced figures'}. "
            + (
                f"From the library's {process} process ({', '.join(sorted(used_process))}): "
                f"{'; '.join(process_refs)}. "
                if used_process
                else ""
            )
            + (
                f"Process defaults used for {', '.join(sorted(set(defaulted)))}."
                if defaulted
                else "No process defaults used."
            )
        ),
    }
    if build is not None:
        out["buildVolume"] = build
    for key in ("leadDays", "qtyRange"):
        if key in default:
            out[key] = copy.deepcopy(default[key])
    return out


def derive_machines(
    config: dict[str, Any],
    lib: MachineSource,
    process_limits: ProcessLimits | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """A copy of ``config`` with library machines in ``context.machines``, and notes."""
    out = copy.deepcopy(config)
    memo: dict[str, dict[str, Any] | None] = {}

    def _limits_for(process: str) -> dict[str, Any] | None:
        if process_limits is None:
            return None
        if process not in memo:
            try:
                memo[process] = process_limits(process)
            except Exception as exc:  # noqa: BLE001 - a library read never fails the write
                logger.warning(
                    "imagine_library_process_limits_failed", process=process, error=str(exc)
                )
                memo[process] = None
        return memo[process]

    if not isinstance(out, dict):
        return out, []
    raw = out.get("process")
    process_cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}
    studio_process = str(process_cfg.get("id") or "")
    chosen = str(process_cfg.get("machine") or "").strip()
    notes: list[str] = []
    built: list[dict[str, Any]] = []
    with tracer.start_as_current_span("imagine.library_machines") as span:
        span.set_attribute("imagine.process", studio_process)
        if chosen:
            try:
                machine = lib.get(chosen)
            except Exception:  # noqa: BLE001 - not a library id: the validator reports it
                machine = None
            if machine is not None:
                model = _model_of(lib, machine)
                process = _process(machine, model)
                if process is None or process != studio_process:
                    notes.append(
                        f'process.machine "{chosen}" is a library {process or machine.kind} '
                        f'machine, but the studio\'s process is "{studio_process}"; choose a '
                        f"{studio_process} machine or change the process"
                    )
                else:
                    entry = studio_machine(machine, model, process, _limits_for(process))
                    built.append(entry)
                    if entry["defaulted"]:
                        notes.append(
                            f'machine "{entry["name"]}" ({chosen}): the library holds no '
                            f"{', '.join(entry['defaulted'])}, so the {process} process defaults "
                            "are used for them; add the machine's own figures to the library"
                        )
        try:
            instances = lib.list(layer="instance")
        except Exception as exc:  # noqa: BLE001 - a library read never fails the write
            span.record_exception(exc)
            logger.warning("imagine_library_machines_list_failed", error=str(exc))
            instances = []
        for inst in instances:
            if not isinstance(inst, MachineInstance) or inst.route != "in_house":
                continue
            if any(m["id"] == inst.id for m in built):
                continue
            model = _model_of(lib, inst)
            if _process(inst, model) == studio_process:
                built.append(
                    studio_machine(inst, model, studio_process, _limits_for(studio_process))
                )
        context = out.setdefault("context", {})
        block = context.get("machines") if isinstance(context.get("machines"), dict) else {}
        own = [
            m
            for m in (block.get("machines") or [])
            if isinstance(m, dict) and m.get("derived_from") != MARK
        ]
        if built or own or "machines" in context:
            context["machines"] = {**block, "machines": [*own, *built]}
        span.set_attribute("imagine.library_machines.built", len(built))
    if built:
        logger.info("imagine_library_machines_derived", machines=[m["id"] for m in built])
    return out, notes
