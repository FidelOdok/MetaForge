"""Map the Imagine studio's head state to the verification service's ConceptRevision.

This is generation-side work: it reads the studio the way the handover
checklist always has (the same helpers) and states the facts. It decides
nothing. Every predicate that turns those facts into pass or fail lives in
MetaForge-Verification, so a threshold can never exist on both sides.

The output is a plain dict in the service's contract (``ConceptRevision`` of
the ``imagine-v1`` profile), ready to send. The ids are the studio's own, made
safe for the service's id alphabet, so a finding's ``target`` maps straight
back to a part, function, assumption or ledger row.
"""

from __future__ import annotations

import re
from typing import Any

from api_gateway.imagine.service import (
    _agent_label,
    _chosen_parts,
    _mfc_from_config,
    _programme,
    _requirements,
    _scenarios,
)

__all__ = ["build_concept_revision", "safe_id"]

_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
_MAX_ID = 100


def safe_id(raw: object) -> str:
    """The service's id alphabet, without changing an id that already fits."""
    text = _UNSAFE.sub("_", str(raw)).strip("_") or "item"
    return text[:_MAX_ID]


class _Ids:
    """Stable, unique, safe ids for one list."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def take(self, raw: object) -> str:
        base = safe_id(raw)
        out, n = base, 2
        while out in self._seen:
            suffix = f"_{n}"
            out, n = f"{base[: _MAX_ID - len(suffix)]}{suffix}", n + 1
        self._seen.add(out)
        return out


def _text(value: Any, limit: int = 500) -> str | None:
    """Presence is what the rules test, so a long value is clipped to the contract's bound."""
    return None if value in (None, "") else str(value)[:limit]


def _quantity(value: Any) -> int | None:
    """A stated quantity as a whole number, or ``None`` when none was stated."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


_FORM_PART_KINDS = ("generated", "primitive", "component")
_MAX_FORM_PARTS = 24


def _form(config: dict[str, Any]) -> dict[str, Any] | None:
    """The authored form as the contract's ``FormFacts``, or ``None`` when there is nothing to say.

    Only an authored form is described. An enclosure form, or no form, sends nothing: the
    contract refuses fields it does not know, so the block must be absent unless it is
    needed, and the service's report for an enclosure studio stays exactly what it was.
    The code of each part stays here; the service is told what each part is and how far to
    trust it as geometry. A part whose kind the contract does not know is left out, not
    given one. If nothing usable remains, no form is sent, because an authored form with
    no parts is something the contract refuses.
    """
    form = config.get("form")
    if not isinstance(form, dict) or form.get("template") != "authored":
        return None
    block = form.get("authored")
    raw = block.get("parts") if isinstance(block, dict) else None
    if not isinstance(raw, list):
        return None
    ids = _Ids()
    parts: list[dict[str, Any]] = []
    body_taken = False
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") not in _FORM_PART_KINDS:
            continue
        if len(parts) >= _MAX_FORM_PARTS:
            break
        is_body = item.get("body") is True and not body_taken
        body_taken = body_taken or is_body
        parts.append({"id": ids.take(item.get("id")), "kind": item["kind"], "body": is_body})
    return {"kind": "authored", "parts": parts} if parts else None


def build_concept_revision(
    project_id: str,
    revision: int,
    config: dict[str, Any],
    collections: dict[str, Any],
    *,
    has_mesh: bool,
) -> dict[str, Any]:
    """The studio's facts as a ``ConceptRevision``."""
    programme = _programme(config)

    ledger_ids = _Ids()
    ledger = []
    for i, row in enumerate(programme.get("ledger") or []):
        row = row if isinstance(row, dict) else {}
        status = ((collections.get("ledger") or {}).get(f"l{i}") or {}).get("status") or row.get(
            "status"
        )
        ledger.append({"id": ledger_ids.take(f"l{i}"), "status": _text(status, 50)})

    assumption_ids = _Ids()
    assumptions = []
    for a in programme.get("assumptions") or []:
        if not isinstance(a, dict):
            continue
        ev = (collections.get("assumptions") or {}).get(str(a.get("id"))) or {}
        if ev.get("confirmed") is True:
            state = "confirmed"
        elif ev.get("accepted") is True:
            state = "accepted"
        else:
            state = "unconfirmed"
        item: dict[str, Any] = {"id": assumption_ids.take(a.get("id")), "state": state}
        if state == "accepted" and ev.get("agent"):
            item["accepted_by"] = _agent_label(ev)[:200]
        assumptions.append(item)

    scenario_ids = _Ids()
    scenarios = []
    for s in _scenarios(config, collections):
        item = {
            "id": scenario_ids.take(s["id"]),
            "origin": "person" if s.get("by") == "user" else "inferred",
            "confirmed": s.get("confirmed") is True,
        }
        if s.get("confirmed") is True and s.get("agent"):
            item["confirmed_by"] = _agent_label(s)[:200]
        scenarios.append(item)

    requirement_ids = _Ids()
    requirements = [
        {
            "id": requirement_ids.take(r["name"]),
            "source": _text(str(r.get("source") or "").strip(), 500),
            "verification_defaulted": bool(r.get("verify_defaulted")),
        }
        for r in _requirements(config, collections)
        if not r["name"].startswith("img_scenario")
    ]

    function_ids = _Ids()
    part_ids = _Ids()
    parts: list[dict[str, Any]] = []
    part_key: dict[int, str] = {}
    functions = []
    for role, part in _chosen_parts(config, collections).items():
        chosen: str | None = None
        if part is not None:
            key = id(part)
            if key not in part_key:
                geometry = part.get("geometry") or {}
                source = (
                    part.get("datasheet")
                    or part.get("url")
                    or (part.get("cost") or {}).get("source")
                    or geometry.get("source")
                )
                interfaces = part.get("interfaces")
                part_key[key] = part_ids.take(part.get("id"))
                parts.append(
                    {
                        "id": part_key[key],
                        "source": _text(source),
                        "retrieved": _text(part.get("retrieved") or part.get("date"), 50),
                        # The checklist tests truthiness, so a non-empty value of any
                        # shape counts as described.
                        "interface_count": (
                            len(interfaces)
                            if isinstance(interfaces, (list, dict))
                            else (1 if interfaces else 0)
                        ),
                        "has_envelope": bool(geometry.get("envelope")),
                    }
                )
            chosen = part_key[key]
        functions.append({"id": function_ids.take(role.removeprefix("fn:")), "part_id": chosen})

    mfc = _mfc_from_config(config)
    declared = programme.get("interfaces")
    declared_count = len(declared) if isinstance(declared, list) else (1 if declared else 0)
    with_interfaces = sum(
        1 for p in _chosen_parts(config, collections).values() if p and p.get("interfaces")
    )

    out: dict[str, Any] = {
        "twin_id": safe_id(project_id),
        "revision_id": f"r{revision}",
        "number": revision,
        "ledger": ledger,
        "assumptions": assumptions,
        "scenarios": scenarios,
        "requirements": requirements,
        "functions": functions,
        "parts": parts,
        "process": {
            "process": _text(mfc.get("selected_process"), 100),
            "material": _text(mfc.get("selected_material"), 100),
            "quantity": _quantity(mfc.get("quantity")),
        },
        "markets": [str(m) for m in programme.get("markets") or []],
        "interface_count": with_interfaces + declared_count,
        "screening_stored": bool((collections.get("screening") or {}).get("latest")),
        "concept_mesh": bool(has_mesh),
    }
    form = _form(config)
    if form is not None:
        out["form"] = form
    return out
