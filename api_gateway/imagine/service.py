"""The Imagine stage: the formgraph studio as a twin item, its events and its handover.

The studio is one definition item per project, ``IMG-STUDIO`` (work product
type ``imagine_studio``). A new config (``PUT /studio``) or a rework is its
next revision. Browser writes (``POST /events``) update the current
revision's working state in place, are idempotent per ``event_id``, and are
mapped into the twin through :class:`~api_gateway.imagine.twin_bridge.TwinBridge`.

The handover gate checks the studio against the checklist in
``docs/imagine-stage.md``, seeds the twin and freezes the revision
read-only. A rework opens the next revision; moving the item's head is what
marks the records made for the frozen revision stale, through the twin's
existing impact path (``twin_core.consistency.record_pins.on_head_moved``).

Verification levels: the studio's checks run in a browser, so nothing it
sends is stored above V2 (Screened). Every record written from here says so,
and fields a browser might use to claim more are dropped on the way in.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError

from api_gateway.imagine import (
    component_link,
    engine_page,
    library_cost,
    library_facts,
    library_geometry,
    library_link,
    library_machines,
    library_materials,
    library_models,
    part_model,
    resolve_part,
    studio_patch,
    verification_client,
    wiring_checks,
    wiring_outputs,
)
from api_gateway.imagine.schemas import (
    COLLECTION_KINDS,
    KIND_COLLECTIONS,
    AcceptResult,
    EventAccepted,
    HandoverChecklist,
    HandoverItem,
    HandoverRecord,
    HandoverResult,
    ImagineEvent,
    StudioResponse,
)
from api_gateway.imagine.twin_bridge import TwinBridge, mfc_field
from api_gateway.imagine.validation import (
    ValidatePost,
    ValidationReport,
    config_findings,
    unavailable_report,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.service")

ITEM_TYPE = "imagine_studio"
STUDIO_KEY = "IMG-STUDIO"
META_KEY = "imagine_studio"
#: The highest verification level anything from the browser is stored at.
MAX_LEVEL = "V2"
#: Keys a browser may not set on anything it sends.
_FORBIDDEN_KEYS = frozenset(
    {
        "verified",
        "verification_level",
        "verificationLevel",
        "level_reached",
        "authority",
        "authority_state",
        "approved",
        "released",
        "baselined",
        "qualified",
    }
)
#: Keys that say an agent decided for a person (FORGE-827). Only the
#: delegated path sets them; anything a browser or an undelegated caller
#: sends under these names is dropped.
_DELEGATION_KEYS = frozenset({"agent", "on_behalf_of", "delegated_by"})
_MAX_EVENT_BYTES = 256 * 1024
_VERIFY = ("test", "analysis", "inspection", "demonstration")
_CATALOGUE_VERIFY = "analysis"
_PART_ROLES_FIXED = ("board", "battery")

_metrics: Any = None


def _count(operation: str, outcome: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_imagine_studio(operation, outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a request
        pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def engine_path(project_id: str) -> str:
    return f"/v1/twin/projects/{project_id}/imagine/engine"


def engine_url(project_id: str) -> str:
    """The engine page's gateway path; signed for ten minutes when auth is on.

    An iframe cannot send a bearer token, so with authentication required the
    path carries a short-lived token of its own (``engine_token``).
    """
    from api_gateway.imagine import engine_token

    path = engine_path(project_id)
    if engine_token.auth_required():
        return f"{path}?t={engine_token.sign(project_id)}"
    return path


_MEASURED_KEYS = (
    "total_area",
    "overhang_area",
    "overhang_share",
    "com",
    "support",
    "overlaps",
    "notes",
)
_MAX_MEASURED_BYTES = 16_000
_MAX_FIT_PAIRS = 24


def _vec3(value: Any) -> list[float]:
    if not (isinstance(value, list) and len(value) == 3):
        raise ValueError("a point must be three numbers")
    return [_number(x) for x in value]


def _clean_fit(value: Any) -> dict[str, Any] | None:
    """The page's fit test (which parts clash on their drawn shapes), in its own shape only."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("fit must be an object")
    pairs = value.get("pairs")
    if not isinstance(pairs, list) or not all(isinstance(p, dict) for p in pairs):
        raise ValueError("fit.pairs must be a list of objects")
    not_closed = value.get("not_closed") or []
    if not isinstance(not_closed, list):
        raise ValueError("fit.not_closed must be a list")
    clean_pairs = []
    for p in pairs[:_MAX_FIT_PAIRS]:
        region = p.get("region")
        if not isinstance(region, dict):
            raise ValueError("a fit pair needs its region")
        clean_pairs.append(
            {
                "a": str(p["a"])[:64],
                "b": str(p["b"])[:64],
                "a_name": str(p.get("a_name") or p["a"])[:120],
                "b_name": str(p.get("b_name") or p["b"])[:120],
                "depth_mm": _number(p["depth_mm"]),
                "points": int(_number(p.get("points") or 0)),
                "region": {"min": _vec3(region.get("min")), "max": _vec3(region.get("max"))},
            }
        )
    return {
        "pairs": clean_pairs,
        "checked_pairs": int(_number(value.get("checked_pairs") or 0)),
        "not_closed": [str(x)[:64] for x in not_closed[:_MAX_FIT_PAIRS]],
        "partial": bool(value.get("partial")),
        "tolerance_mm": _number(value["tolerance_mm"]),
        "step_mm": _number(value["step_mm"]),
    }


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError("a measurement must be a number")
    if value in (float("inf"), float("-inf")):
        raise ValueError("a measurement must be finite")
    return float(value)


def _clean_measured(value: Any) -> dict[str, Any]:
    """The look's measurements, reduced to the fields the page reports and checked for shape.

    Raises ``ValueError`` when the value is not that shape. Anything else a caller adds (a
    ``verified`` flag, an ``authority``) is dropped, never stored.
    """
    if not isinstance(value, dict):
        raise ValueError("the measurement must be an object")
    out: dict[str, Any] = {}
    for key in ("total_area", "overhang_area", "overhang_share"):
        out[key] = _number(value[key]) if value.get(key) is not None else None
    com = value.get("com")
    if com is None:
        out["com"] = None
    elif isinstance(com, list) and len(com) == 3:
        out["com"] = [_number(x) for x in com]
    else:
        raise ValueError("com must be three numbers")
    support = value.get("support")
    if support is None:
        out["support"] = None
    elif isinstance(support, dict):
        out["support"] = {k: _number(support[k]) for k in ("points", "margin_mm", "tip_angle_deg")}
    else:
        raise ValueError("support must be an object")
    overlaps = value.get("overlaps")
    if not isinstance(overlaps, list):
        raise ValueError("overlaps must be a list")
    out["overlaps"] = [
        {"a": str(o["a"])[:64], "b": str(o["b"])[:64], "volume_mm3": _number(o["volume_mm3"])}
        for o in overlaps
        if isinstance(o, dict)
    ]
    if len(out["overlaps"]) != len(overlaps):
        raise ValueError("each overlap must be an object")
    notes = value.get("notes")
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise ValueError("notes must be a list of text")
    out["notes"] = [n[:300] for n in notes[:12]]
    try:
        fit = _clean_fit(value.get("fit"))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"fit is not the shape the page makes: {exc}") from exc
    if fit is not None:
        out["fit"] = fit
    wires = value.get("wires")
    if wires is not None:
        if not isinstance(wires, list) or not all(isinstance(w, dict) for w in wires):
            raise ValueError("wires must be a list of objects")
        out["wires"] = [
            {
                "id": str(w["id"])[:64],
                "length_mm": _number(w["length_mm"]) if w.get("length_mm") is not None else None,
                "fits": w.get("fits") if isinstance(w.get("fits"), bool) else None,
            }
            for w in wires[:200]
        ]
    return out


class ImagineError(Exception):
    """A request the Imagine service refuses, with the HTTP status that says why."""

    def __init__(self, message: str, status: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


class HandoverNotReady(ImagineError):
    def __init__(self, items: list[HandoverItem]) -> None:
        super().__init__("The studio is not ready to hand over to Develop.", 409)
        #: The whole checklist; the ones holding the handover are ``missing``.
        self.items = items
        self.missing = [i for i in items if i.status == "missing"]


@dataclass
class _Head:
    revision: int
    node_id: UUID
    metadata: dict[str, Any]
    state: dict[str, Any]
    updated_at: str | None

    @property
    def ref(self) -> str:
        return f"{STUDIO_KEY}@{self.revision}"


def sanitize(value: Any, depth: int = 0) -> Any:
    """Drop every key a browser could use to claim more than V2."""
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            str(k): sanitize(v, depth + 1)
            for k, v in value.items()
            if str(k) not in _FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [sanitize(v, depth + 1) for v in value[:500]]
    if isinstance(value, str):
        return value[:20000]
    return value


def _empty_collections() -> dict[str, dict[str, Any]]:
    return {c: {} for c in COLLECTION_KINDS}


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    s = s[:limit].strip("_") or "x"
    return s if s[0].isalpha() else f"p_{s}"


# ── reading the studio config ────────────────────────────────────────────


def _context(config: dict[str, Any]) -> dict[str, Any]:
    ctx = config.get("context")
    return ctx if isinstance(ctx, dict) else {}


def _programme(config: dict[str, Any]) -> dict[str, Any]:
    pg = config.get("programme")
    return pg if isinstance(pg, dict) else {}


def _parts(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    comps = _context(config).get("components") or {}
    return {
        str(p["id"]): p for p in (comps.get("parts") or []) if isinstance(p, dict) and p.get("id")
    }


def _decisions(config: dict[str, Any]) -> list[dict[str, Any]]:
    opts = _context(config).get("options") or {}
    return [d for d in (opts.get("decisions") or []) if isinstance(d, dict) and d.get("id")]


def _scenarios(config: dict[str, Any], collections: dict[str, Any]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    sources = (config.get("scenarios") or []) + (
        (_context(config).get("scenarios") or {}).get("scenarios") or []
    )
    for s in sources:
        if isinstance(s, dict) and s.get("id"):
            out[str(s["id"])] = dict(s)
    for sid, s in (collections.get("scenarios") or {}).items():
        merged = {**out.get(sid, {}), **s, "id": sid}
        # A scenario a person added or touched in the studio is theirs.
        merged.setdefault("by", "user")
        out[sid] = merged
    return list(out.values())


def _picked(dec: dict[str, Any], collections: dict[str, Any]) -> str | None:
    choice = (collections.get("choices") or {}).get(str(dec["id"])) or {}
    pick = choice.get("pick") or dec.get("pick")
    return str(pick) if pick else None


def _chosen_parts(
    config: dict[str, Any], collections: dict[str, Any]
) -> dict[str, dict[str, Any] | None]:
    """For each function (and the board and battery), the part chosen for it."""
    parts = _parts(config)
    wanted = [f"fn:{f}" for f in (config.get("functions") or [])]
    # The board and the battery count once the context names real ones; a
    # catalogue board or cell is the engine's stand-in, not a chosen part.
    roles = {str(p.get("role")) for p in parts.values()}
    wanted += [r for r in _PART_ROLES_FIXED if r in roles]
    chosen: dict[str, dict[str, Any] | None] = {role: None for role in wanted}
    for dec in _decisions(config):
        if dec.get("kind") != "part":
            continue
        pick = _picked(dec, collections)
        part = parts.get(pick or "")
        if part and part.get("role") in chosen and not part.get("excluded"):
            chosen[str(part["role"])] = part
    # A role with exactly one candidate and no decision is chosen by default.
    for role in chosen:
        if chosen[role] is None:
            cands = [p for p in parts.values() if p.get("role") == role and not p.get("excluded")]
            if len(cands) == 1:
                chosen[role] = cands[0]
    return chosen


def _form_refs(config: dict[str, Any]) -> set[str]:
    """The context parts the authored form draws (its component parts' ``component_ref``)."""
    form = config.get("form") if isinstance(config.get("form"), dict) else {}
    authored = (form or {}).get("authored")
    parts = authored.get("parts") if isinstance(authored, dict) else None
    return {
        str(p["component_ref"])
        for p in parts or []
        if isinstance(p, dict) and isinstance(p.get("component_ref"), str)
    }


def _parts_in_use(
    config: dict[str, Any], collections: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Every part the product uses: the one chosen for each role, and every part the form
    draws (two LED channels are two drivers, though a role picks one) (FORGE-903)."""
    parts = _parts(config)
    used: dict[str, tuple[str, dict[str, Any]]] = {
        str(p["id"]): (role, p) for role, p in _chosen_parts(config, collections).items() if p
    }
    for pid in sorted(_form_refs(config)):
        p = parts.get(pid)
        if p and not p.get("excluded") and pid not in used:
            used[pid] = (str(p.get("role") or ""), p)
    return list(used.values())


def _machine_library() -> Any:
    """The gateway's machine library (the studio's machines come from it, FORGE-879)."""
    from api_gateway.library.routes import get_machine_library

    return get_machine_library()


def _process_limits(process: str) -> dict[str, Any] | None:
    """The library process's design-guide limits for a studio process, or None."""
    from api_gateway.library.catalog_routes import get_catalog
    from twin_core.library.catalog import CatalogError, CatalogNotFoundError

    try:
        limits: dict[str, Any] = get_catalog().process_limits(process, None)
    except (CatalogNotFoundError, CatalogError):
        return None
    return limits


def _part_gaps(part: dict[str, Any]) -> list[str]:
    geometry = part.get("geometry") or {}
    source = next(
        (
            c
            for c in (
                part.get("datasheet"),
                part.get("url"),
                (part.get("cost") or {}).get("source"),
                geometry.get("source"),
            )
            if part_model.is_real_source(c)
        ),
        None,
    )
    gaps = []
    if not source:
        gaps.append("source")
    if not (part.get("retrieved") or part.get("date")):
        gaps.append("date")
    if not part.get("interfaces"):
        gaps.append("interfaces")
    if not part_model.size_is_read(geometry):
        gaps.append("3D envelope")
    return gaps


def _requirements(config: dict[str, Any], collections: dict[str, Any]) -> list[dict[str, Any]]:
    """Every studio requirement in the form the constraint set takes."""
    cat_reqs = engine_page.catalogue().get("requirements") or {}
    out: dict[str, dict[str, Any]] = {}
    for r in config.get("requirements") or []:
        if not isinstance(r, dict) or r.get("pri") not in ("Must", "Should"):
            continue
        spec = cat_reqs.get(r.get("id"), {})
        text = str(spec.get("text") or r.get("id"))
        for k, v in (r.get("values") or {}).items():
            text = text.replace(f"[{k}]", str(v))
        out[f"req:{r['id']}"] = {
            "name": f"img_req_{_slug(r['id'])}",
            "text": text,
            "priority": r.get("pri"),
            "verify": r.get("verify") if r.get("verify") in _VERIFY else _CATALOGUE_VERIFY,
            "verify_defaulted": r.get("verify") not in _VERIFY,
            "source": r.get("source") or "",
            "acceptance": spec.get("proved_by") or "",
        }
    for c in _programme(config).get("customRequirements") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        out[f"custom:{c['id']}"] = {
            "name": f"img_custom_{_slug(c['id'])}",
            "text": str(c.get("text") or ""),
            "priority": c.get("pri", "Should"),
            "verify": c.get("verify") if c.get("verify") in _VERIFY else "test",
            "verify_defaulted": c.get("verify") not in _VERIFY,
            "source": c.get("source") or "",
            "acceptance": " ".join(str(c.get(k) or "") for k in ("measure", "target")).strip(),
        }
    for rid, ev in (collections.get("requirements") or {}).items():
        key = f"custom:{rid}" if f"custom:{rid}" in out else f"studio:{rid}"
        base = out.get(key, {})
        text = str(ev.get("text") or base.get("text") or "")
        if not text:
            continue
        verify = ev.get("verify") if ev.get("verify") in _VERIFY else base.get("verify", "test")
        out[key] = {
            **base,
            "name": base.get("name") or f"img_studio_{_slug(rid)}",
            "text": text,
            "priority": ev.get("pri") or base.get("priority", "Should"),
            "verify": verify,
            "verify_defaulted": base.get("verify_defaulted", ev.get("verify") not in _VERIFY),
            "source": ev.get("source") or base.get("source") or "",
            "acceptance": base.get("acceptance", ""),
        }
    for s in _scenarios(config, collections):
        text = str(s.get("text") or "")
        if not text:
            continue
        out[f"scenario:{s['id']}"] = {
            "name": f"img_scenario_{_slug(s['id'])}",
            "text": f"Scenario: {text}",
            "priority": "Should",
            "verify": "demonstration",
            "verify_defaulted": False,
            "source": (
                "person, Imagine studio"
                if s.get("by") == "user"
                else f"agent {s['agent']} on behalf of {s['on_behalf_of']}, Imagine studio"
                if s.get("agent") and s.get("on_behalf_of")
                else "inferred"
            ),
            "acceptance": ", ".join(str(x) for x in s.get("requires") or []),
        }
    return list(out.values())


#: The environment's limits that every part must be rated for (FORGE-903): the studio
#: key, the part spec it binds, the comparison and the words. A part's rated minimum
#: must reach down to the coldest use, and its rated maximum up to the hottest.
_ENV_LIMITS: tuple[tuple[str, str, str, str], ...] = (
    ("opMin", "operating_temp_min_c", "<=", "down to {v} °C, the coldest the product is used in"),
    ("opMax", "operating_temp_max_c", ">=", "up to {v} °C, the hottest the product is used in"),
)


def _recorded_requirements(
    config: dict[str, Any], collections: dict[str, Any]
) -> list[dict[str, Any]]:
    """What the requirements constraint set records: the person's requirements, and the
    environment as typed limits for parts. The handover checklist reads only the former."""
    return [*_requirements(config, collections), *_environment_requirements(config).values()]


def _environment_requirements(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The operating environment as typed requirements every part is held against.

    Only figures the studio states as numbers; Check compares each part's rated
    temperatures with them (spec margins), so a part rated for less than the product's
    environment is flagged when it is chosen, not at a gate.
    """
    env = config.get("environment") if isinstance(config.get("environment"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for key, metric, op, words in _ENV_LIMITS:
        value = (env or {}).get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[f"env:{key}"] = {
            "name": f"img_env_{metric}",
            "text": f"Every part is rated to operate {words.format(v=value)}.",
            "priority": "Must",
            "verify": "inspection",
            "verify_defaulted": False,
            "source": f"the operating environment set in the Imagine studio (environment.{key})",
            "acceptance": "the part's datasheet operating temperature range",
            "metric": metric,
            "operator": op,
            "limit": float(value),
            "unit": "degC",
            "target_node_type": "BOMItem",
        }
    return out


def _mfc_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """The manufacturing context the config states, in MFC field names."""
    cat = engine_page.catalogue()
    process = config.get("process") or {}
    proc = (cat.get("processes") or {}).get(process.get("id")) or {}
    product = config.get("product") or {}
    out: dict[str, Any] = {
        "selected_process": proc.get("name") or process.get("id"),
        "selected_material": process.get("material"),
        "quantity": process.get("qty"),
        "finish": process.get("finish"),
        "fastening": process.get("fixings"),
        "intended_use": product.get("summary") or product.get("tagline") or product.get("name"),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


# ── the service ──────────────────────────────────────────────────────────


class ImagineService:
    """The Imagine stage over one twin."""

    def __init__(
        self,
        twin: Any,
        project_backend: Any = None,
        bridge: Any = None,
        verification: Any = None,
    ) -> None:
        self.twin = twin
        self.project_backend = project_backend
        self.bridge = bridge if bridge is not None else TwinBridge(twin, project_backend)
        self._verification = verification
        self._locks: dict[str, asyncio.Lock] = {}

    def _verification_client(self) -> Any:
        """The service that decides validation, or ``None`` when none is configured."""
        if self._verification is not None:
            return self._verification
        return verification_client.from_env()

    def _lock(self, project_id: str) -> asyncio.Lock:
        lock = self._locks.get(project_id)
        if lock is None:
            lock = self._locks[project_id] = asyncio.Lock()
        return lock

    # -- storage ----------------------------------------------------------

    async def _head(self, project_id: str) -> _Head | None:
        from twin_core.items import find_item, supports_items

        if self.twin is None or not supports_items(self.twin):
            raise ImagineError("this twin cannot keep the Imagine studio", 503)
        item = await find_item(self.twin, STUDIO_KEY, UUID(project_id))
        if item is None or item.head_node_id is None:
            return None
        wp = await self.twin.get_work_product(item.head_node_id)
        if wp is None:
            return None
        meta = dict(getattr(wp, "metadata", None) or {})
        state = copy.deepcopy(meta.get(META_KEY) or {})
        state.setdefault("collections", _empty_collections())
        for c in COLLECTION_KINDS:
            state["collections"].setdefault(c, {})
        state.setdefault("events", {})
        updated = getattr(wp, "updated_at", None)
        return _Head(
            revision=int(item.head_revision),
            node_id=item.head_node_id,
            metadata=meta,
            state=state,
            updated_at=updated.isoformat() if updated else None,
        )

    async def _write_revision(
        self, project_id: str, state: dict[str, Any], *, actor: str, reason: str
    ) -> _Head:
        from api_gateway.twin.item_revisions import finish_definition_revision
        from twin_core.items import plan_revision
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        pid = UUID(project_id)
        name = str(((state.get("config") or {}).get("product") or {}).get("name") or "Studio")
        title = f"Imagine studio: {name}"[:200]
        plan = await plan_revision(
            self.twin,
            item_type=ITEM_TYPE,
            name=title,
            project_id=pid,
            author=actor,
            item_key=STUDIO_KEY,
            change_reason=reason,
            run_id=None,
        )
        if plan is None:
            raise ImagineError("this twin cannot keep the Imagine studio", 503)
        state["updated_at"] = _now()
        state["max_level"] = MAX_LEVEL
        content = json.dumps(state.get("config") or {}, sort_keys=True, default=str).encode()
        now = datetime.now(UTC)
        wp_id = uuid4()
        metadata = {META_KEY: state, "source": "imagine.studio", **plan.stamp()}
        created = await self.twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=title,
                type=WorkProductType.IMAGINE_STUDIO,
                domain="systems",
                file_path="",
                content_hash=hashlib.sha256(content).hexdigest(),
                format="json",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=actor,
                project_id=pid,
            )
        )
        node_id = getattr(created, "id", wp_id)
        result: dict[str, Any] = {}
        await finish_definition_revision(self.twin, plan, node_id, name=title, result=result)
        if self.project_backend is not None:
            try:
                await self.project_backend.link_work_product(
                    project_id, str(node_id), title, ITEM_TYPE
                )
            except Exception as exc:  # noqa: BLE001 - the link is best-effort
                logger.warning("imagine_project_link_failed", error=str(exc))
        revision = int(result.get("revision") or plan.revision)
        return _Head(revision, node_id, metadata, state, state["updated_at"])

    async def _save(self, head: _Head) -> None:
        head.state["updated_at"] = _now()
        meta = {**head.metadata, META_KEY: head.state}
        await self.twin.update_work_product(head.node_id, {"metadata": meta})
        head.metadata = meta
        head.updated_at = head.state["updated_at"]

    def _response(self, project_id: str, head: _Head | None) -> StudioResponse:
        if head is None:
            return StudioResponse(
                project_id=project_id, status="none", revision=0, engine_url=engine_url(project_id)
            )
        st = head.state
        handover = st.get("handover")
        return StudioResponse(
            project_id=project_id,
            status="handed_over" if st.get("status") == "handed_over" else "draft",
            revision=head.revision,
            config_ref=head.ref,
            engine_url=engine_url(project_id),
            config=st.get("config"),
            updated_at=head.updated_at,
            handover=HandoverRecord(**handover) if handover else None,
            collections=st.get("collections") or {},
            warnings=list(st.get("warnings") or []),
        )

    # -- studio -----------------------------------------------------------

    async def get_studio(self, project_id: str) -> StudioResponse:
        with tracer.start_as_current_span("imagine.get_studio") as span:
            span.set_attribute("project_id", project_id)
            return self._response(project_id, await self._head(project_id))

    async def put_studio(
        self, project_id: str, config: dict[str, Any], *, actor: str
    ) -> StudioResponse:
        with tracer.start_as_current_span("imagine.put_studio") as span:
            span.set_attribute("project_id", project_id)
            return await self._put(project_id, config, actor=actor, span=span)

    async def patch_studio(
        self, project_id: str, base_revision: int, changes: Any, *, actor: str
    ) -> StudioResponse:
        """The next revision: the head's config with ``changes`` applied (studio_patch).

        Refused (409) when the head is no longer ``base_revision``, so a change is never
        applied to a config its author did not read; (400) when a change cannot be applied;
        and (422) when the changed config fails the formgraph checks, exactly as a full
        write is. Nothing is written when it is refused.
        """
        with tracer.start_as_current_span("imagine.patch_studio") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("studio.base_revision", base_revision)
            head = await self._head(project_id)
            if head is None or not isinstance(head.state.get("config"), dict):
                _count("patch", "refused")
                raise ImagineError(
                    "There is no studio to change yet; write the first config with "
                    "twin.imagine_put_studio.",
                    404,
                )
            if head.revision != base_revision:
                _count("patch", "refused")
                raise ImagineError(
                    f"The studio is at revision {head.revision}, not {base_revision}; read it "
                    f"again (twin.imagine_get_studio) and make the change against "
                    f"revision {head.revision}.",
                    409,
                )
            try:
                config, applied = studio_patch.apply_changes(head.state["config"], changes)
            except studio_patch.PatchError as exc:
                _count("patch", "refused")
                raise ImagineError(str(exc), 400) from exc
            span.set_attribute("studio.changes", len(applied))
            out = await self._put(
                project_id,
                config,
                actor=actor,
                span=span,
                expect_revision=base_revision,
                reason="studio change from the agent",
                metric="patch",
            )
            logger.info(
                "imagine_studio_patched",
                project_id=project_id,
                base_revision=base_revision,
                revision=out.revision,
                changes=applied,
            )
            return out

    async def adopt_component(
        self,
        project_id: str,
        base_revision: int,
        adopt: dict[str, Any],
        *,
        actor: str,
    ) -> StudioResponse:
        """Take what Develop decided for one component into the studio (FORGE-903).

        ``adopt`` is a component thread's ``adopt``: ``{kind: "part", part_id?, node_id,
        mpn, manufacturer, library?, datasheet?}``. A part the studio already has takes
        Develop's order code, maker and library entry; when it gains a library entry its
        old datasheet and size are dropped, so the entry's own are derived again on this
        write. A part Develop added is added to the studio's parts. A board is refused:
        the studio's parts are bought parts with a source. The next revision is written
        as any change is: checked, and refused (409) when the studio moved past
        ``base_revision`` or is handed over.
        """
        with tracer.start_as_current_span("imagine.adopt_component") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("studio.base_revision", base_revision)
            head = await self._head(project_id)
            if head is None or not isinstance(head.state.get("config"), dict):
                _count("adopt", "refused")
                raise ImagineError("There is no studio to adopt into yet.", 404)
            if head.state.get("status") == "handed_over":
                _count("adopt", "refused")
                raise ImagineError(
                    f"{head.ref} is handed over and read-only; reopen Imagine to adopt "
                    "what Develop decided.",
                    409,
                )
            if head.revision != base_revision:
                _count("adopt", "refused")
                raise ImagineError(
                    f"The studio is at revision {head.revision}, not {base_revision}; "
                    "read the components again and adopt against the current revision.",
                    409,
                )
            config = copy.deepcopy(head.state["config"])
            context = config.setdefault("context", {})
            comps = context.setdefault("components", {"schemaVersion": 1, "parts": []})
            parts = comps.setdefault("parts", [])
            stamp = {"from": "develop", "at": _now(), "by": actor}
            if str(adopt.get("kind") or "part") != "part":
                _count("adopt", "refused")
                raise ImagineError(
                    "Only a bought part can be adopted: the studio's parts are real parts "
                    "with a source, and a board is designed in Develop.",
                    400,
                )
            mpn = str(adopt.get("mpn") or "").strip()
            if not mpn:
                raise ImagineError("A part to adopt needs its order code.", 400)
            library = str(adopt.get("library") or "").strip()
            fields: dict[str, Any] = {
                "mpn": mpn,
                "manufacturer": str(adopt.get("manufacturer") or "").strip(),
                "adopted": {**stamp, "node_id": adopt.get("node_id"), "mpn": mpn},
            }
            if library:
                fields["library"] = library
            elif adopt.get("datasheet"):
                fields["datasheet"] = str(adopt["datasheet"])
                fields["retrieved"] = str(adopt.get("recorded_at") or _now())[:10]
            part_id = str(adopt.get("part_id") or "").strip()
            if part_id:
                target = next(
                    (p for p in parts if isinstance(p, dict) and str(p.get("id")) == part_id),
                    None,
                )
                if target is None:
                    raise ImagineError(f"The studio has no part {part_id}.", 404)
                if library and target.get("library") != library:
                    # The entry's own datasheet and size replace the concept's.
                    for stale in ("datasheet", "retrieved", "geometry"):
                        target.pop(stale, None)
                target.update({k: v for k, v in fields.items() if v})
                what = f"{part_id} as {mpn}"
            else:
                pid = _slug(mpn, 40)
                if any(str(p.get("id")) == pid for p in parts if isinstance(p, dict)):
                    raise ImagineError(f"The studio already has a part {pid}.", 409)
                role = str(adopt.get("role") or "").strip()
                if not role.startswith("fn:") and role not in _PART_ROLES_FIXED:
                    role = "other"
                parts.append(
                    {
                        "id": pid,
                        "role": role,
                        "name": str(adopt.get("name") or f"{fields['manufacturer']} {mpn}").strip(),
                        **{k: v for k, v in fields.items() if v},
                    }
                )
                what = f"part {mpn}"
            out = await self._put(
                project_id,
                config,
                actor=actor,
                span=span,
                expect_revision=base_revision,
                reason=f"adopted from Develop: {what}",
                metric="adopt",
            )
            logger.info(
                "imagine_component_adopted",
                project_id=project_id,
                base_revision=base_revision,
                revision=out.revision,
                what=what,
            )
            return out

    async def add_mesh_reference(
        self,
        project_id: str,
        *,
        work_product_id: str,
        ref_id: str,
        title: str,
        role: str = "context",
        note: str | None = None,
        units: str | None = None,
        up: str | None = None,
        at: list[float] | None = None,
        rotate: list[float] | None = None,
        color: str | None = None,
        opacity: float | None = None,
        actor: str,
        step_converter: Any = None,
    ) -> StudioResponse:
        """Put a twin model in the studio as a see-through mesh reference (MetaForge).

        A hosted studio reads no files, so a mesh reference has to arrive packed. This
        reads the work product's stored file, turns a STEP into a mesh with the same
        OCCT converter the viewer and renders use (millimetres, Z up), reduces it for
        display and writes it into ``context.references`` as the studio's next revision.
        A reference with the same id is replaced. The full mesh's size is kept in
        ``stats``; the page and its checks use that.

        The engine centres the mesh on X and Z and puts its lowest point at Y = 0, then
        moves it to ``at`` and turns it by ``rotate`` (degrees, about X, Y, Z). ``origin``
        records which point of the file that is, in studio axes (mm, Y up), so an offset
        worked out from the full-resolution model lands where it was meant to.
        ``color`` and ``opacity`` replace the role's see-through colour for the whole mesh.
        """
        import os
        import tempfile

        from api_gateway.imagine.formgraph import fg_refs
        from api_gateway.twin.blob_store import resolve_work_product_blob

        with tracer.start_as_current_span("imagine.add_mesh_reference") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("work_product_id", work_product_id)
            if not fg_refs.SLUG.match(ref_id or ""):
                raise ImagineError(
                    f"reference id {ref_id!r} must be a slug (lowercase letters, digits, dashes)"
                )
            if role not in fg_refs.ROLES:
                raise ImagineError(f"role must be one of {', '.join(sorted(fg_refs.ROLES))}")
            head = await self._head(project_id)
            if head is None or not isinstance(head.state.get("config"), dict):
                _count("reference", "refused")
                raise ImagineError(
                    "There is no studio to add a reference to yet; write the first config "
                    "with twin.imagine_put_studio.",
                    404,
                )
            try:
                wp = await self.twin.get_work_product(UUID(str(work_product_id)))
            except ValueError as exc:
                raise ImagineError(f"invalid work_product_id {work_product_id!r}") from exc
            if wp is None:
                raise ImagineError(f"no work product {work_product_id!r}", 404)
            try:
                content, filename = resolve_work_product_blob(wp)
            except Exception as exc:  # noqa: BLE001 - HTTPException from the resolver
                detail = getattr(exc, "detail", None) or str(exc)
                raise ImagineError(
                    f"{wp.name!r} has no readable model file: {detail}", 404
                ) from exc
            ext = os.path.splitext(filename)[1].lower().lstrip(".") or str(wp.format or "").lower()
            span.set_attribute("reference.source_format", ext)
            if ext in ("step", "stp"):
                from api_gateway.twin.render_recorder import RenderError, convert_step_to_glb

                convert = step_converter or convert_step_to_glb
                try:
                    content = await asyncio.to_thread(convert, content, filename)
                except RenderError as exc:
                    raise ImagineError(str(exc), 502) from exc
                # Measured: the converter keeps the STEP's millimetres and its Z-up axes.
                ext, units, up = "glb", units or "mm", up or "z"
            elif ext not in ("glb", "stl", "obj"):
                raise ImagineError(
                    f"{wp.name!r} is a {ext or 'format-less'} file; a mesh reference needs a "
                    "STEP, GLB, STL or OBJ"
                )
            if units is not None and units not in fg_refs.UNITS:
                raise ImagineError(f"units must be one of {', '.join(sorted(fg_refs.UNITS))}")
            if up is not None and up not in ("y", "z"):
                raise ImagineError("up must be y or z")
            for name, vec in (("at", at), ("rotate", rotate)):
                if vec is not None and not (
                    isinstance(vec, list)
                    and len(vec) == 3
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vec)
                ):
                    raise ImagineError(f"{name} must be [x, y, z]")
            if color is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color)):
                raise ImagineError("color must be a hex colour like #3A5F7D")
            if opacity is not None:
                numeric = isinstance(opacity, (int, float)) and not isinstance(opacity, bool)
                if not numeric or not 0.05 <= float(opacity) <= 1:
                    raise ImagineError("opacity must be between 0.05 and 1")

            def _read(data: bytes) -> tuple[Any, str, str]:
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, f"model.{ext}")
                    with open(path, "wb") as fh:
                        fh.write(data)
                    tris, used_units, used_up = fg_refs.read_mesh(path, units, up)
                if not tris:
                    raise ImagineError(f"{wp.name!r} has no triangles to show")
                return (
                    {"stats": fg_refs.mesh_stats(tris), "mesh": fg_refs.reduce_mesh(tris)},
                    used_units,
                    used_up,
                )

            try:
                packed, used_units, used_up = await asyncio.to_thread(_read, content)
            except ImagineError:
                raise
            except Exception as exc:  # noqa: BLE001 - a malformed mesh is the caller's input
                raise ImagineError(f"{wp.name!r} could not be read as a mesh: {exc}") from exc
            ref: dict[str, Any] = {
                "id": ref_id,
                "kind": "mesh",
                "role": role,
                "title": title,
                "units": used_units,
                "up": used_up,
                "fileName": filename,
                "source": f"twin work product {work_product_id}",
                **packed,
            }
            if note:
                ref["note"] = note
            lo, hi = packed["stats"]["min"], packed["stats"]["max"]
            ref["origin"] = [round((lo[0] + hi[0]) / 2, 2), lo[1], round((lo[2] + hi[2]) / 2, 2)]
            for key, value in (
                ("at", at),
                ("rotate", rotate),
                ("color", color),
                ("opacity", opacity),
            ):
                if value is not None:
                    ref[key] = value
            config = copy.deepcopy(head.state["config"])
            context = config.setdefault("context", {})
            refs = context.get("references")
            if not isinstance(refs, dict):
                refs = {"schemaVersion": 1, "references": []}
                context["references"] = refs
            listed = [r for r in refs.get("references") or [] if r.get("id") != ref_id]
            refs["references"] = [*listed, ref]
            span.set_attribute("reference.triangles", packed["stats"]["triangles"])
            out = await self._put(
                project_id,
                config,
                actor=actor,
                span=span,
                expect_revision=head.revision,
                reason=f"mesh reference {ref_id} from work product {work_product_id}",
                metric="reference",
            )
            logger.info(
                "imagine_mesh_reference_added",
                project_id=project_id,
                reference=ref_id,
                work_product_id=work_product_id,
                triangles=packed["stats"]["triangles"],
                size_mm=packed["stats"]["size"],
                revision=out.revision,
            )
            return out

    async def _put(
        self,
        project_id: str,
        config: dict[str, Any],
        *,
        actor: str,
        span: Any,
        expect_revision: int | None = None,
        reason: str = "studio config from the agent",
        metric: str = "put",
    ) -> StudioResponse:
        # A linked part's size comes from its library entry first (FORGE-877), so the
        # rungs below draw only what the library cannot size.
        lookup = library_cost.cached(library_cost.catalog_entry)
        # Its maker, order code, datasheet and title too, where the part leaves them empty
        # (FORGE-896): the agent need not read the entry to link a part.
        config, identified = await asyncio.to_thread(
            library_facts.derive_part_facts, config, lookup
        )
        config, sized = await asyncio.to_thread(
            library_geometry.derive_part_geometry, config, lookup
        )
        # The material's figures come from the material library (FORGE-880).
        config, material_notes = await asyncio.to_thread(
            library_materials.derive_material,
            config,
            library_cost.cached(library_materials.catalog_material),
        )
        # The machine and the in-house machines for the process come from the machine
        # library (FORGE-879), so process.machine can name the owner's own printer.
        config, machined = await asyncio.to_thread(
            library_machines.derive_machines, config, _machine_library(), _process_limits
        )
        # A part with no size gets a labelled 3D body, so every part exists in 3D.
        # Reading a library and a model is file work: off the event loop.
        config, drawn = await asyncio.to_thread(
            resolve_part.fill_part_bodies,
            config,
            kicad_config=resolve_part.kicad_config_from_env(),
        )
        # A component in the form that names its real part is drawn from that part: with
        # the part's library model when its entry has one, else as its bounding box.
        model = library_models.cached(library_models.catalog_model)
        modelled_entries = await asyncio.to_thread(library_models.linked_models, config, model)
        config, linked = component_link.expand_component_parts(
            config, has_model=modelled_entries.__contains__
        )
        # Library models the parts name travel with the revision (the page has no network).
        config, modelled = await asyncio.to_thread(library_models.embed, config, model)
        # A linked part with no cost of its own is priced from the library's confirmed offers.
        config, priced = await asyncio.to_thread(library_cost.price_parts, config, lookup)
        # Real parts the component library does not hold yet: register once, reuse later.
        library = await asyncio.to_thread(
            library_link.library_notes, config, library_link.catalog_exists
        )
        drawn = [
            *identified,
            *material_notes,
            *sized,
            *machined,
            *drawn,
            *linked,
            *modelled,
            *priced,
            *library,
        ]
        # The wiring as a whole: unwired power, floating inputs, wires too thin (FORGE-886).
        wiring = wiring_checks.wiring_findings(_context(config))
        try:
            checked = engine_page.validate_config(config)
        except engine_page.ConfigError as exc:
            _count(metric, "refused")
            # ``errors`` and ``warnings`` stay for the dashboard; ``findings``
            # is the same shape POST /validate returns, so an agent reads one
            # format. There is no verdict here: the write is refused before
            # anything is judged, and verdicts are the service's to give.
            findings = config_findings(exc.errors, exc.warnings, None)
            raise ImagineError(
                "The config does not pass the formgraph checks.",
                422,
                {
                    "errors": exc.errors,
                    "warnings": exc.warnings,
                    "findings": [f.model_dump(mode="json") for f in findings],
                },
            ) from exc
        async with self._lock(project_id):
            head = await self._head(project_id)
            if head is not None and head.state.get("status") == "handed_over":
                _count(metric, "refused")
                raise ImagineError(
                    f"{head.ref} is handed over and read-only; rework it to change the config.",
                    409,
                )
            now = head.revision if head is not None else 0
            if expect_revision is not None and now != expect_revision:
                _count(metric, "refused")
                raise ImagineError(
                    f"The studio moved to revision {now} while this change was checked; "
                    f"read it again and make the change against revision {now}.",
                    409,
                )
            state = {
                "config": checked.config,
                "status": "draft",
                "collections": (head.state["collections"] if head else _empty_collections()),
                "events": (head.state["events"] if head else {}),
                "handover": None,
                "rework": head.state.get("rework") if head else None,
                "warnings": [*checked.warnings, *drawn, *wiring],
            }
            new = await self._write_revision(project_id, state, actor=actor, reason=reason)
        span.set_attribute("studio.revision", new.revision)
        _count(metric, "ok")
        self._publish(project_id, f"put-{new.revision}", f"Studio config {new.ref} recorded")
        logger.info(
            "imagine_studio_put",
            project_id=project_id,
            ref=new.ref,
            warnings=len(checked.warnings),
        )
        return self._response(project_id, new)

    # -- measurements -----------------------------------------------------

    async def record_measurement(self, project_id: str, revision: int, measured: Any) -> None:
        """Keep what a look measured against the revision it measured.

        Only the gateway's own headless browser calls this (``twin.imagine_look``); no route
        accepts a measurement, so a person's browser cannot claim one. The record belongs to
        one revision: a newer write starts a state without it, so a stale figure is never
        carried forward. Writing it makes no new revision.
        """
        try:
            cleaned = _clean_measured(measured)
        except (ValueError, KeyError, TypeError) as exc:
            _count("measure", "refused")
            raise ImagineError(
                f"The measurement is not in the shape the page reports: {exc}", 422
            ) from exc
        if len(json.dumps(cleaned)) > _MAX_MEASURED_BYTES:
            _count("measure", "refused")
            raise ImagineError("The measurement is too large (16 KB at most).", 413)
        async with self._lock(project_id):
            head = await self._head(project_id)
            if head is None:
                _count("measure", "refused")
                raise ImagineError("No studio yet: an agent puts a config first.", 404)
            if head.state.get("status") == "handed_over":
                _count("measure", "refused")
                raise ImagineError(f"{head.ref} is handed over and read-only.", 409)
            if head.revision != revision:
                _count("measure", "refused")
                raise ImagineError(
                    f"The studio is at revision {head.revision}, not {revision}: a measurement "
                    "belongs to the revision it measured.",
                    409,
                )
            head.state["measured"] = {
                "revision": head.revision,
                "at": _now(),
                "method": "headless look run by the gateway",
                "measured": cleaned,
            }
            await self._save(head)
        _count("measure", "ok")

    async def get_measurement(self, project_id: str) -> dict[str, Any] | None:
        """The measurement recorded for the head revision, or ``None``."""
        head = await self._head(project_id)
        if head is None:
            return None
        record = head.state.get("measured")
        if not isinstance(record, dict) or record.get("revision") != head.revision:
            return None
        return dict(record)

    async def engine_html(self, project_id: str) -> str:
        with tracer.start_as_current_span("imagine.engine_page") as span:
            span.set_attribute("project_id", project_id)
            head = await self._head(project_id)
            if head is None:
                _count("engine", "refused")
                raise ImagineError("No studio yet: an agent puts a config first.", 404)
            config = head.state.get("config") or {}
            if ((config.get("form") or {}).get("template")) != "authored":
                _count("engine", "refused")
                raise ImagineError(
                    "This studio uses the enclosure template, which was removed: every form "
                    "is authored now. Write a new studio with form.template authored.",
                    410,
                )
            checked = engine_page.validate_config(config)
            _count("engine", "ok")
            return engine_page.render_engine(checked.compiled)

    # -- events -----------------------------------------------------------

    async def post_event(
        self,
        project_id: str,
        event: ImagineEvent,
        *,
        actor: str,
        delegated: dict[str, Any] | None = None,
        from_agent: bool = False,
    ) -> EventAccepted:
        """Map one studio write into the twin.

        ``delegated`` (FORGE-827) is set when an agent accepts an assumption
        or confirms a scenario for a person under the owner's delegation (see
        :func:`_delegation`). The write then names the agent and the person,
        and an assumption the agent "confirms" is recorded as accepted: it
        stays an assumption, never a person's statement. ``from_agent`` marks
        any other write from the agent surface, so a scenario it touches is
        not read as the person's.
        """
        with tracer.start_as_current_span("imagine.event") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("event.kind", event.kind)
            span.set_attribute("imagine.delegated", bool(delegated))
            if len(json.dumps(event.data, default=str)) > _MAX_EVENT_BYTES:
                _count("event", "refused")
                raise ImagineError("The event is too large (256 KB at most).", 413)
            async with self._lock(project_id):
                head = await self._head(project_id)
                if head is None:
                    _count("event", "refused")
                    raise ImagineError("No studio yet: an agent puts a config first.", 404)
                seen = head.state["events"].get(event.event_id)
                if seen is not None:
                    _count("event", "duplicate")
                    return EventAccepted(
                        mapped_to=list(seen.get("mapped_to") or []), duplicate=True
                    )
                if head.state.get("status") == "handed_over":
                    _count("event", "refused")
                    raise ImagineError(
                        f"{head.ref} is handed over and read-only; rework it to change it.", 409
                    )
                data = sanitize(event.data)
                inner = data.get("data")
                if isinstance(inner, dict) and set(data) <= {"id", "data", "collection"}:
                    data = {**inner, "id": data.get("id", inner.get("id"))}
                doc_id = str(data.get("id") or event.event_id)[:128]
                collection = KIND_COLLECTIONS[event.kind]
                previous = head.state["collections"][collection].get(doc_id)
                data = {k: v for k, v in data.items() if k not in _DELEGATION_KEYS}
                if event.kind == "form_seen":
                    # A person looking at the form is not something an agent can say for them,
                    # and the revision is the server's, never the page's.
                    if from_agent or delegated:
                        _count("event", "refused")
                        raise ImagineError(
                            "Seeing the form is the person's own act; an agent cannot record it.",
                            403,
                        )
                    doc_id = "latest"
                    data = {
                        "seen": data.get("seen") is True,
                        "revision": head.revision,
                        "at": datetime.now(UTC).isoformat(),
                    }
                if delegated:
                    data = _delegated_write(event.kind, data, previous, delegated)
                elif from_agent and event.kind == "scenario":
                    data["by"] = "agent"
                head.state["collections"][collection][doc_id] = data
                try:
                    mapped = await self._map(project_id, head, event.kind, doc_id, data, actor)
                except ImagineError:
                    raise
                except Exception as exc:
                    span.record_exception(exc)
                    _count("event", "failed")
                    logger.error(
                        "imagine_event_failed",
                        project_id=project_id,
                        kind=event.kind,
                        event_id=event.event_id,
                        error=str(exc),
                    )
                    if previous is None:
                        head.state["collections"][collection].pop(doc_id, None)
                    raise ImagineError(
                        "The twin did not take the event; retry with the same event_id.", 502
                    ) from exc
                head.state["events"][event.event_id] = {
                    "kind": event.kind,
                    "id": doc_id,
                    "mapped_to": mapped,
                    "at": _now(),
                    "by": actor,
                    "revision": head.revision,
                    **(
                        {"agent": delegated["agent"], "on_behalf_of": delegated["on_behalf_of"]}
                        if delegated
                        else {}
                    ),
                }
                await self._save(head)
            _count("event", "ok")
            logger.info(
                "imagine_event_mapped",
                project_id=project_id,
                kind=event.kind,
                event_id=event.event_id,
                mapped_to=mapped,
                agent=delegated["agent"] if delegated else None,
                on_behalf_of=delegated["on_behalf_of"] if delegated else None,
            )
            return EventAccepted(mapped_to=mapped)

    async def _map(
        self,
        project_id: str,
        head: _Head,
        kind: str,
        doc_id: str,
        data: dict[str, Any],
        actor: str,
    ) -> list[str]:
        config = head.state.get("config") or {}
        cols = head.state["collections"]
        if kind == "choice":
            return [await self._map_choice(project_id, head, doc_id, data)]
        if kind in ("scenario", "requirement"):
            ref = await self.bridge.record_requirements(
                project_id,
                _recorded_requirements(config, cols),
                f"{kind} {doc_id} in the Imagine studio",
            )
            return [ref] if ref else []
        if kind == "ledger":
            return await self._map_ledger(project_id, config, doc_id, data)
        if kind == "assumption_confirmed":
            return await self._map_assumption(project_id, head, doc_id, data, actor)
        if kind == "context_edit":
            return await self._map_context_edit(project_id, head, doc_id, data, actor)
        if kind == "screening":
            return [await self._map_screening(project_id, head, data)]
        return []

    async def _map_choice(
        self, project_id: str, head: _Head, doc_id: str, data: dict[str, Any]
    ) -> str:
        config = head.state.get("config") or {}
        pick = str(data.get("pick") or "").strip()
        if not pick:
            raise ImagineError("A choice needs a pick.", 422)
        if doc_id.startswith("mfg-"):
            label, candidates = f"Maker for {doc_id[4:]}", []
        else:
            dec = next((d for d in _decisions(config) if str(d["id"]) == doc_id), None)
            if dec is None:
                raise ImagineError(f"There is no option {doc_id!r} in the studio config.", 422)
            candidates = [str(c) for c in dec.get("candidates") or []]
            if candidates and pick not in candidates:
                raise ImagineError(f"{pick!r} is not a candidate for {doc_id!r}.", 422)
            label = str(dec.get("label") or doc_id)
        parts = _parts(config)
        name = str((parts.get(pick) or {}).get("name") or pick)
        ref, _ = await self.bridge.record_decision(
            project_id,
            title=f"{label}: {name}",
            rationale=(
                f"A person chose {name} for {label} in the Imagine studio ({head.ref}). "
                "It rests on browser screening only (V2 at most); Develop confirms it "
                "against exact CAD, the schematic and analysis."
            ),
            alternatives=[c for c in candidates if c != pick],
            depends_on=[head.ref],
        )
        return ref

    async def _map_ledger(
        self, project_id: str, config: dict[str, Any], doc_id: str, data: dict[str, Any]
    ) -> list[str]:
        ledger = _programme(config).get("ledger") or []
        try:
            index = int(data.get("index", doc_id.lstrip("l")))
        except (TypeError, ValueError):
            raise ImagineError(f"Ledger entry {doc_id!r} names no statement.", 422) from None
        if not 0 <= index < len(ledger):
            raise ImagineError(f"There is no ledger statement {index}.", 422)
        entry = ledger[index] if isinstance(ledger[index], dict) else {}
        text = str(entry.get("text") or data.get("text") or "").strip()
        if not text:
            return []
        ref = await self.bridge.record_entity(
            project_id,
            entity_type="intent",
            statement=text,
            title=f"Brief: {text[:80]}",
            extra={
                "ledger_index": index,
                "ledger_status": str(data.get("status") or entry.get("status") or "unreviewed"),
                "maps": entry.get("maps") or [],
                "statement_source": "brief",
                "verification_level": MAX_LEVEL,
            },
            item_key=f"INT-IMG-L{index}",
            reason="intent ledger reviewed in the Imagine studio",
        )
        return [ref]

    async def _map_assumption(
        self, project_id: str, head: _Head, doc_id: str, data: dict[str, Any], actor: str
    ) -> list[str]:
        config = head.state.get("config") or {}
        assumption = next(
            (
                a
                for a in _programme(config).get("assumptions") or []
                if isinstance(a, dict) and str(a.get("id")) == doc_id
            ),
            None,
        )
        if assumption is None:
            raise ImagineError(f"There is no assumption {doc_id!r} in the studio config.", 422)
        confirmed = data.get("confirmed") is True
        accepted = data.get("accepted") is True
        status = "stated" if confirmed else ("assumed" if accepted else "open")
        refs = [
            await self.bridge.record_entity(
                project_id,
                entity_type="assumption",
                statement=str(assumption.get("text") or doc_id),
                extra={
                    "assumption_id": doc_id,
                    "affects": assumption.get("ref"),
                    "status": status,
                    "confirmed_by": actor if confirmed else None,
                    # FORGE-827: accepted by an agent for a person, never by them.
                    "accepted_by_agent": data.get("agent"),
                    "on_behalf_of": data.get("on_behalf_of"),
                    "verification_level": MAX_LEVEL,
                },
                reason="assumption reviewed in the Imagine studio",
            )
        ]
        field_name = mfc_field(str(assumption.get("ref") or ""))
        value = _mfc_from_config(config).get(field_name or "")
        if field_name and value is not None:
            mfc = await self.bridge.write_context(
                project_id,
                {field_name: (value, "stated" if confirmed else "assumed")},
                actor=actor,
                ref=head.ref,
                reason=f"assumption {doc_id} {status} in the Imagine studio",
            )
            if mfc:
                refs.append(mfc)
        return refs

    async def _map_context_edit(
        self, project_id: str, head: _Head, doc_id: str, data: dict[str, Any], actor: str
    ) -> list[str]:
        kind = str(data.get("kind") or "")
        fields: dict[str, tuple[Any, str]] = {}
        if kind == "machine" and isinstance(data.get("machine"), dict):
            m = data["machine"]
            label = ", ".join(
                str(x)
                for x in (m.get("name"), m.get("process"), m.get("source"))
                if x not in (None, "")
            )
            if label:
                fields["machine_or_supplier_profile"] = (label, "stated")
        elif kind == "value":
            name = mfc_field(str(data.get("field") or ""))
            if name and data.get("value") not in (None, ""):
                fields[name] = (data.get("value"), "stated")
        if fields:
            ref = await self.bridge.write_context(
                project_id,
                fields,
                actor=actor,
                ref=f"{head.ref}#contextEdits/{doc_id}",
                reason=f"context edit {doc_id} in the Imagine studio",
            )
            if ref:
                return [ref]
        # A correction to one part's data stays on the studio until handover.
        return [f"{head.ref}#contextEdits/{doc_id}"]

    async def _map_screening(self, project_id: str, head: _Head, data: dict[str, Any]) -> str:
        rules = [r for r in data.get("rules") or [] if isinstance(r, dict)][:300]
        attention = [r for r in rules if r.get("status") not in ("pass", "na", None)]
        product = ((head.state.get("config") or {}).get("product") or {}).get("name") or "product"
        engine = str(data.get("engine") or "formgraph")
        return await self.bridge.record_entity(
            project_id,
            entity_type="evidence",
            statement=(
                f"Browser screening of {product}: {len(rules)} checks, "
                f"{len(attention)} need attention. Preliminary guidance only (V2)."
            ),
            title=f"Imagine screening: {product}",
            extra={
                "verification_level": MAX_LEVEL,
                "dfm_status": "preliminary_guidance",
                "evidence_kind": "screening",
                "rule_version": str(data.get("rule_version") or engine),
                "engine": engine,
                "process": data.get("process"),
                "rules": rules,
                "requirements": [r for r in data.get("requirements") or [] if isinstance(r, dict)][
                    :300
                ],
                "studio_ref": head.ref,
            },
            reason="screening results from the Imagine studio",
        )

    # -- handover ---------------------------------------------------------

    async def checklist(self, project_id: str) -> HandoverChecklist:
        with tracer.start_as_current_span("imagine.handover_status") as span:
            span.set_attribute("project_id", project_id)
            head = await self._head(project_id)
            if head is None:
                return HandoverChecklist(
                    ready=False,
                    items=[
                        HandoverItem(
                            id="studio",
                            label="Studio generated",
                            status="missing",
                            detail="No studio yet: an agent puts a config first.",
                        )
                    ],
                )
            return await self._checklist(project_id, head)

    async def validate(
        self, project_id: str, request: ValidatePost | None = None
    ) -> ValidationReport:
        """Ask the verification service for the report, and render what it decides.

        The rules, the verdict and the level are the service's (profile
        ``imagine-v1``); nothing here judges. Read-only: nothing is written and
        no assumption is accepted. The gateway states the studio's facts
        (:mod:`concept_revision`), sends the ``config_valid`` result it still
        owns (an interim exception), and returns the report untouched.

        A config in the request is checked instead of the head's (409 once
        handed over). A config the service finds invalid is a 422 whose body is
        its report. A service that is unconfigured, unreachable or failing is a
        ``blocked_external`` report with one external finding, never a local
        fallback.
        """
        # Imported here: concept_revision reads this module's helpers.
        from api_gateway.imagine.concept_revision import build_concept_revision

        req = request or ValidatePost()
        with tracer.start_as_current_span("imagine.validate") as span:
            span.set_attribute("project_id", project_id)
            head = await self._head(project_id)
            if head is None:
                _count("validate", "refused")
                raise ImagineError("No studio yet: an agent puts a config first.", 404)
            if req.revision is not None and req.revision != head.revision:
                _count("validate", "refused")
                raise ImagineError(
                    f"Only the head revision ({head.revision}) can be validated.",
                    422,
                    {"revision": req.revision, "head": head.revision},
                )
            if req.config is not None and head.state.get("status") == "handed_over":
                _count("validate", "refused")
                raise ImagineError(
                    f"{head.ref} is handed over; validate the frozen revision instead.", 409
                )
            config = req.config if req.config is not None else head.state.get("config") or {}

            client = self._verification_client()
            if client is None:
                _count("validate", "unconfigured")
                logger.warning("imagine_verification_unconfigured", project_id=project_id)
                return unavailable_report(
                    project_id,
                    head.revision,
                    "The verification service is not configured "
                    f"({verification_client.URL_ENV} is unset).",
                )

            # Interim exception: the formgraph config check is still this
            # gateway's. The service folds the result into its verdict and
            # never assumes a config is valid on its own.
            try:
                checked = engine_page.validate_config(config)
                supplied = config_findings([], checked.warnings, head.revision)
            except engine_page.ConfigError as exc:
                supplied = config_findings(exc.errors, exc.warnings, head.revision)

            has_mesh = await self.bridge.has_concept_mesh(project_id)
            body: dict[str, Any] = {
                "revision": build_concept_revision(
                    project_id,
                    head.revision,
                    config,
                    head.state["collections"],
                    has_mesh=has_mesh,
                    measured=head.state.get("measured"),
                ),
                "supplied": [f.model_dump(mode="json") for f in supplied],
            }
            if req.scope:
                body["scope"] = [
                    r.model_dump(mode="json", include={"kind", "id", "revision"}) for r in req.scope
                ]
            if req.gates:
                body["gates"] = list(req.gates)

            try:
                raw = await client.validate(body)
            except verification_client.VerificationUnavailable as exc:
                _count("validate", "unavailable")
                return unavailable_report(project_id, head.revision, exc.reason)
            except verification_client.VerificationRefused as exc:
                detail = exc.detail
                if isinstance(detail, dict) and detail.get("bad_refs"):
                    _count("validate", "refused")
                    raise ImagineError(
                        "A scope ref names nothing in this studio.",
                        422,
                        {"bad_refs": detail["bad_refs"]},
                    ) from exc
                # The service called our request malformed: a bug here, not an outage.
                _count("validate", "error")
                raise ImagineError(
                    "The verification service refused the request.", 502, {"detail": detail}
                ) from exc

            try:
                report = ValidationReport.model_validate(
                    {**raw, "project_id": project_id, "computed_at": _now(), "stale": False}
                )
            except ValidationError as exc:
                _count("validate", "unavailable")
                logger.error("imagine_verification_report_unreadable", error=str(exc)[:300])
                return unavailable_report(
                    project_id,
                    head.revision,
                    "The verification service answered a report this gateway does not understand.",
                )
            span.set_attribute("validate.verdict", report.verdict)
            if report.verdict == "invalid":
                _count("validate", "invalid")
                raise ImagineError(
                    "The config does not pass the formgraph checks.",
                    422,
                    report.model_dump(mode="json"),
                )
            _count("validate", "ok")
            return report

    async def _checklist(self, project_id: str, head: _Head) -> HandoverChecklist:
        config = head.state.get("config") or {}
        cols = head.state["collections"]
        items: list[HandoverItem] = []

        def add(item_id: str, label: str, status: str, detail: str) -> None:
            items.append(HandoverItem(id=item_id, label=label, status=status, detail=detail))  # type: ignore[arg-type]

        # 1. the intent ledger
        ledger = _programme(config).get("ledger") or []
        open_rows = []
        for i, row in enumerate(ledger):
            row = row if isinstance(row, dict) else {}
            st = (cols["ledger"].get(f"l{i}") or {}).get("status") or row.get("status")
            if not st or st in ("unreviewed", "unrepresentable"):
                open_rows.append(i)
        if not ledger:
            add("ledger", "Ledger complete", "missing", "No intent ledger in the config.")
        elif open_rows:
            add(
                "ledger",
                "Ledger complete",
                "missing",
                f"{len(open_rows)} of {len(ledger)} statements still to review.",
            )
        else:
            add("ledger", "Ledger complete", "done", f"All {len(ledger)} statements reviewed.")

        # 2. assumptions
        asm = [a for a in _programme(config).get("assumptions") or [] if isinstance(a, dict)]
        unconfirmed: list[str] = []
        accepted: list[str] = []
        by_agent: dict[str, list[str]] = {}
        for a in asm:
            ev = cols["assumptions"].get(str(a.get("id"))) or {}
            if ev.get("confirmed") is True:
                continue
            (accepted if ev.get("accepted") is True else unconfirmed).append(str(a.get("id")))
            if ev.get("accepted") is True and ev.get("agent"):
                by_agent.setdefault(_agent_label(ev), []).append(str(a.get("id")))
        if unconfirmed:
            add(
                "assumptions",
                "Assumptions confirmed or accepted",
                "missing",
                "Still to confirm: " + ", ".join(unconfirmed[:10]),
            )
        elif accepted:
            add(
                "assumptions",
                "Assumptions confirmed or accepted",
                "assumed",
                "Accepted as assumptions: " + ", ".join(accepted[:10]) + _by_agent_note(by_agent),
            )
        else:
            add(
                "assumptions",
                "Assumptions confirmed or accepted",
                "done",
                f"{len(asm)} confirmed." if asm else "No assumptions recorded.",
            )

        # 3. scenario tests
        scen = _scenarios(config, cols)
        inferred = [
            str(s["id"]) for s in scen if s.get("by") != "user" and s.get("confirmed") is not True
        ]
        scen_by_agent: dict[str, list[str]] = {}
        for s in scen:
            if s.get("confirmed") is True and s.get("agent"):
                scen_by_agent.setdefault(_agent_label(s), []).append(str(s["id"]))
        if not scen:
            add("scenarios", "Scenario tests confirmed", "missing", "No scenario tests yet.")
        elif inferred:
            add(
                "scenarios",
                "Scenario tests confirmed",
                "missing",
                "Inferred, not yet confirmed by a person: " + ", ".join(inferred[:10]),
            )
        else:
            add(
                "scenarios",
                "Scenario tests confirmed",
                "done",
                f"{len(scen)} confirmed." + _by_agent_note(scen_by_agent),
            )

        # 4. requirements with source and verification method
        reqs = [r for r in _requirements(config, cols) if not r["name"].startswith("img_scenario")]
        no_source = [r["name"] for r in reqs if not str(r.get("source") or "").strip()]
        defaulted = [r["name"] for r in reqs if r.get("verify_defaulted")]
        if not reqs:
            add("requirements", "Requirements with source and method", "missing", "None yet.")
        elif no_source:
            add(
                "requirements",
                "Requirements with source and method",
                "missing",
                "No source for: " + ", ".join(n.split("_", 2)[-1] for n in no_source[:10]),
            )
        elif defaulted:
            add(
                "requirements",
                "Requirements with source and method",
                "assumed",
                f"{len(reqs)} with a source; {len(defaulted)} use the catalogue's default "
                "verification method.",
            )
        else:
            add(
                "requirements",
                "Requirements with source and method",
                "done",
                f"{len(reqs)} with a source and a method.",
            )

        # 5. one chosen part per function
        chosen = _chosen_parts(config, cols)
        no_part = [role for role, p in chosen.items() if p is None]
        gaps = {
            str(p.get("id")): _part_gaps(p)
            for p in chosen.values()
            if p is not None and _part_gaps(p)
        }
        if not chosen:
            add("parts", "One chosen part per function", "missing", "No functions in the config.")
        elif no_part or gaps:
            bits = []
            if no_part:
                bits.append("no part for " + ", ".join(r.removeprefix("fn:") for r in no_part))
            for pid, g in list(gaps.items())[:6]:
                bits.append(f"{pid} lacks {', '.join(g)}")
            add("parts", "One chosen part per function", "missing", "; ".join(bits))
        else:
            add(
                "parts",
                "One chosen part per function",
                "done",
                f"{len(chosen)} parts, each with source, date, interfaces and an envelope.",
            )

        # 6. manufacturing context
        mfc = _mfc_from_config(config)
        need = [f for f in ("selected_process", "selected_material", "quantity") if f not in mfc]
        if need:
            add(
                "manufacturing_context",
                "Manufacturing context recorded",
                "missing",
                "No " + ", ".join(n.replace("_", " ") for n in need),
            )
        else:
            add(
                "manufacturing_context",
                "Manufacturing context recorded",
                "done",
                f"{mfc['selected_process']}, {mfc['selected_material']}, {mfc['quantity']} units.",
            )

        # 7. concept assembly, and the form seen. A person having seen this revision of the form
        # satisfies it, or an illustrative concept mesh does (spec 5.1, concept.form_seen). The mesh
        # is optional: nothing downstream reads it and it can never be evidence.
        mesh = await self.bridge.has_concept_mesh(project_id)
        seen_doc = (cols.get("formSeen") or {}).get("latest") or {}
        seen = bool(seen_doc.get("seen")) and seen_doc.get("revision") == head.revision
        parts_ok = bool(chosen) and not no_part
        if parts_ok and (mesh or seen):
            how = (
                f"a person has seen this form (revision {head.revision})"
                if seen
                else "an illustrative concept mesh exists"
            )
            if seen and mesh:
                how += "; an illustrative concept mesh also exists"
            add(
                "concept_assembly",
                "Concept assembly and concept mesh",
                "done",
                f"Component ids are assigned at handover; {how}.",
            )
        else:
            missing = []
            if not parts_ok:
                missing.append("a chosen part for every function")
            if not (mesh or seen):
                missing.append(
                    "a person to have seen the form (use 'I have seen this form' in the studio), "
                    "or an illustrative concept mesh"
                )
            add(
                "concept_assembly",
                "Concept assembly and concept mesh",
                "missing",
                "Needs " + " and ".join(missing),
            )

        # 8. proposed interface contracts
        iface = [p for p in chosen.values() if p and p.get("interfaces")]
        if iface or _programme(config).get("interfaces"):
            add(
                "interfaces",
                "Proposed interface contracts",
                "done",
                f"{len(iface)} parts with interfaces; contracts are proposed at handover.",
            )
        else:
            add("interfaces", "Proposed interface contracts", "missing", "No interfaces described.")

        # 9. screening stored as V2
        scr = cols["screening"].get("latest")
        if scr:
            add(
                "screening",
                "Screening results stored (V2)",
                "done",
                f"Screened with {scr.get('rule_version') or 'formgraph'}.",
            )
        else:
            add(
                "screening",
                "Screening results stored (V2)",
                "missing",
                "Open the studio so the engine runs its checks.",
            )

        # 10. wiring: every power and signal input wired, each wire thick enough (FORGE-886)
        w_status, w_detail = wiring_checks.checklist_item(_context(config))
        add("wiring", "Parts wired and checked", w_status, w_detail)

        ready = all(i.status != "missing" for i in items)
        return HandoverChecklist(ready=ready, items=items)

    async def accept(
        self,
        project_id: str,
        op_id: str,
        *,
        assumptions: list[str],
        scenarios: list[str],
        agent: str,
        on_behalf_of: str,
    ) -> AcceptResult:
        """Accept assumptions and confirm scenarios for a person (FORGE-827).

        Only under the owner's delegation (:func:`_delegation`, 403 without
        it). Each is an ordinary studio event, idempotent per ``op_id`` and
        id, recorded as the agent's for the person: an assumption is
        accepted (it stays an assumption), a scenario is confirmed. The
        checklist then reads them as done, and its detail says an agent
        accepted them, never that a person confirmed them.
        """
        with tracer.start_as_current_span("imagine.accept") as span:
            span.set_attribute("project_id", project_id)
            span.set_attribute("imagine.agent", agent)
            delegated = _delegation("acceptance", agent, on_behalf_of)
            if not assumptions and not scenarios:
                _count("acceptance", "refused")
                raise ImagineError("Name at least one assumption or scenario to accept.", 422)
            head = await self._head(project_id)
            if head is None:
                _count("acceptance", "refused")
                raise ImagineError("No studio yet: an agent puts a config first.", 404)
            config = head.state.get("config") or {}
            known_asm = {
                str(a.get("id"))
                for a in _programme(config).get("assumptions") or []
                if isinstance(a, dict)
            }
            known_scen = {str(x["id"]) for x in _scenarios(config, head.state["collections"])}
            unknown = [a for a in assumptions if a not in known_asm] + [
                x for x in scenarios if x not in known_scen
            ]
            if unknown:
                _count("acceptance", "refused")
                raise ImagineError(
                    "Not in the studio config: " + ", ".join(unknown[:10]),
                    422,
                    {"known_assumptions": sorted(known_asm), "known_scenarios": sorted(known_scen)},
                )
            mapped: list[str] = []
            for kind, ids in (("assumption_confirmed", assumptions), ("scenario", scenarios)):
                for item_id in ids:
                    event = ImagineEvent(
                        event_id=f"{op_id}:{kind[:4]}:{item_id}"[:128],
                        kind=kind,  # type: ignore[arg-type]
                        data={"id": item_id, "accepted": True}
                        if kind == "assumption_confirmed"
                        else {"id": item_id, "confirmed": True},
                    )
                    out = await self.post_event(
                        project_id, event, actor=delegated["by"], delegated=delegated
                    )
                    mapped.extend(out.mapped_to)
            _count("acceptance", "ok")
            self._publish(
                project_id,
                f"accept-{_slug(op_id, 24)}",
                "Imagine: "
                + ", ".join(
                    bit
                    for bit in (
                        f"assumptions {', '.join(assumptions)} accepted" if assumptions else "",
                        f"scenarios {', '.join(scenarios)} confirmed" if scenarios else "",
                    )
                    if bit
                )
                + _delegated_note(delegated),
            )
            logger.info(
                "imagine_accepted_by_agent",
                project_id=project_id,
                assumptions=assumptions,
                scenarios=scenarios,
                agent=delegated["agent"],
                on_behalf_of=delegated["on_behalf_of"],
            )
            return AcceptResult(
                assumptions=list(assumptions),
                scenarios=list(scenarios),
                mapped_to=mapped,
                agent=delegated["agent"],
                on_behalf_of=delegated["on_behalf_of"],
                checklist=await self.checklist(project_id),
            )

    async def handover(
        self,
        project_id: str,
        op_id: str,
        *,
        actor: str,
        agent: str | None = None,
        on_behalf_of: str | None = None,
    ) -> HandoverResult:
        """Freeze the studio and seed the twin.

        ``agent`` (FORGE-824) marks a handover an agent makes for a person
        under the owner's delegation; see :func:`_delegation`.
        """
        with tracer.start_as_current_span("imagine.handover") as span:
            span.set_attribute("project_id", project_id)
            delegated = _delegation("handover", agent, on_behalf_of)
            if delegated:
                actor = delegated["by"]
                span.set_attribute("imagine.agent", str(agent))
            async with self._lock(project_id):
                head = await self._head(project_id)
                if head is None:
                    _count("handover", "refused")
                    raise ImagineError("No studio yet: an agent puts a config first.", 404)
                done = head.state.get("handover")
                if head.state.get("status") == "handed_over" and done:
                    if done.get("op_id") == op_id:
                        _count("handover", "duplicate")
                        return HandoverResult(
                            revision=head.revision, created=list(done.get("created") or [])
                        )
                    _count("handover", "refused")
                    raise ImagineError(f"{head.ref} is already handed over.", 409)
                check = await self._checklist(project_id, head)
                if not check.ready:
                    _count("handover", "refused")
                    raise HandoverNotReady(list(check.items))
                try:
                    created = await self._seed(project_id, head, actor)
                except ImagineError:
                    raise
                except Exception as exc:
                    span.record_exception(exc)
                    _count("handover", "failed")
                    logger.error("imagine_handover_failed", project_id=project_id, error=str(exc))
                    raise ImagineError(
                        "The twin did not take the handover; retry with the same op_id.", 502
                    ) from exc
                head.state["status"] = "handed_over"
                head.state["handover"] = {
                    "op_id": op_id,
                    "revision": head.revision,
                    "at": _now(),
                    "by": actor,
                    "created": created,
                    **delegated,
                }
                await self._save(head)
            span.set_attribute("handover.created", len(created))
            _count("handover", "ok")
            self._publish(
                project_id,
                f"handover-{head.revision}",
                f"Imagine handed over to Develop at {head.ref} ({len(created)} records)"
                + _delegated_note(delegated),
            )
            logger.info(
                "imagine_handed_over",
                project_id=project_id,
                ref=head.ref,
                created=len(created),
                agent=agent if delegated else None,
                on_behalf_of=on_behalf_of if delegated else None,
            )
            return HandoverResult(revision=head.revision, created=created)

    async def _seed(self, project_id: str, head: _Head, actor: str) -> list[str]:
        """Write what Develop starts from; returns every reference created."""
        from twin_core.models.interface_contract import (
            ContractKind,
            CoupledParameter,
            InterfaceContract,
            ValueBasis,
        )

        config = head.state.get("config") or {}
        cols = head.state["collections"]
        created: list[str] = []

        ref = await self.bridge.record_requirements(
            project_id, _recorded_requirements(config, cols), f"handover of {head.ref}"
        )
        if ref:
            created.append(ref)

        assumed_fields = {k: (v, "assumed") for k, v in _mfc_from_config(config).items()}
        for a in _programme(config).get("assumptions") or []:
            if isinstance(a, dict) and (cols["assumptions"].get(str(a.get("id"))) or {}).get(
                "confirmed"
            ):
                name = mfc_field(str(a.get("ref") or ""))
                if name and name in assumed_fields:
                    assumed_fields[name] = (assumed_fields[name][0], "stated")
        mfc = await self.bridge.write_context(
            project_id, assumed_fields, actor=actor, ref=head.ref, reason=f"handover of {head.ref}"
        )
        if mfc:
            created.append(mfc)

        ledger = _programme(config).get("ledger") or []
        for i, row in enumerate(ledger):
            if not isinstance(row, dict) or not row.get("text"):
                continue
            st = (cols["ledger"].get(f"l{i}") or {}).get("status") or row.get("status")
            if st == "out-of-scope":
                continue
            created += await self._map_ledger(
                project_id, config, f"l{i}", {"index": i, "status": st}
            )

        chosen = {r: p for r, p in _chosen_parts(config, cols).items() if p is not None}
        process = config.get("process") or {}
        product = config.get("product") or {}
        enclosure = f"{product.get('name') or 'Product'} enclosure"
        asm_parts = [
            {
                "name": enclosure,
                "source": "made",
                "material": str(process.get("material") or ""),
                "process": str(_mfc_from_config(config).get("selected_process") or ""),
                "quantity": 1,
                "provenance": "intended",
            }
        ]
        envelopes: dict[str, Any] = {}
        seen_names = {enclosure}
        for role, p in chosen.items():
            name = str(p.get("name") or p.get("id"))
            if name in seen_names:
                name = f"{name} ({p.get('id')})"
            seen_names.add(name)
            asm_parts.append(
                {
                    "name": name,
                    "source": "bought",
                    "material": "",
                    "process": "",
                    "quantity": 1,
                    "provenance": "intended",
                    # FORGE-903: the thread back to the studio part.
                    "studio_part": str(p.get("id") or ""),
                    "role": role,
                    "mpn": str(p.get("mpn") or ""),
                    "library": str(p.get("library") or ""),
                }
            )
            envelopes[name] = {
                "part_id": p.get("id"),
                "role": role,
                "mpn": p.get("mpn"),
                "source": p.get("datasheet") or p.get("url") or (p.get("cost") or {}).get("source"),
                "retrieved": p.get("retrieved") or p.get("date"),
                "envelope_mm": (p.get("geometry") or {}).get("envelope"),
                "fidelity": (p.get("geometry") or {}).get("fidelity"),
                "size_basis": (p.get("geometry") or {}).get("size_basis"),
            }
        lines = [
            f"The concept assembly chosen in the Imagine studio ({head.ref}). Parts and "
            "envelopes come from datasheets read during Imagine; nothing here is above V2.",
            "",
            "| Part | Source | Retrieved | Envelope mm |",
            "|---|---|---|---|",
        ]
        for name, e in envelopes.items():
            env = "x".join(str(x) for x in e.get("envelope_mm") or [])
            lines.append(
                f"| {name} | {e.get('source') or ''} | {e.get('retrieved') or ''} | {env} |"
            )
        dec_ref, dec = await self.bridge.record_decision(
            project_id,
            title=f"Imagine concept assembly: {product.get('name') or 'product'}",
            rationale="\n".join(lines),
            depends_on=[head.ref],
            assembly={"parts": asm_parts, "connections": []},
        )
        created.append(dec_ref)
        ids = dec.get("component_ids") or {}
        created += [str(cid) for cid in (ids.values() if isinstance(ids, dict) else ids)]
        head.state["components"] = {
            "decision": dec_ref,
            "component_ids": dec.get("component_ids"),
            "envelopes": envelopes,
        }
        # FORGE-903: Develop starts from the concept's parts, as candidates it confirms
        # or replaces, each already linked to the studio parts it fills.
        created += await self.bridge.record_candidates(
            project_id, _parts_in_use(config, cols), f"handover of {head.ref}"
        )

        # Proposed interface contracts: never overwrite one Develop already holds.
        from api_gateway.twin.interface_contracts import get_contract

        cad_params: list[CoupledParameter] = []
        bus_params: list[CoupledParameter] = []
        for name, e in envelopes.items():
            env = e.get("envelope_mm")
            if isinstance(env, list) and len(env) == 3:
                cad_params.append(
                    CoupledParameter(
                        name=f"env_{_slug(str(e.get('part_id') or name), 50)}",
                        label=f"{name} envelope (L x W x H)"[:200],
                        value=env,
                        unit="mm",
                        owner="mechanical",
                        basis=ValueBasis.ESTIMATED,
                        note="From the part's datasheet, read in Imagine (V2).",
                    )
                )
        for p in chosen.values():
            for itf in p.get("interfaces") or []:
                if not isinstance(itf, dict) or not itf.get("bus"):
                    continue
                pname = f"bus_{_slug(str(p.get('id')), 30)}_{_slug(str(itf.get('id') or 'x'), 25)}"
                if any(x.name == pname for x in bus_params):
                    continue
                bus_params.append(
                    CoupledParameter(
                        name=pname,
                        label=f"{p.get('name') or p.get('id')}: {itf.get('id')}"[:200],
                        value=str(itf.get("bus")),
                        owner="electronics",
                        basis=ValueBasis.ESTIMATED,
                        note=f"direction {itf.get('dir') or 'n/a'}; from Imagine (V2)",
                    )
                )
        # The firmware pin map from the connection list goes first, so the 60-parameter cap
        # trims bus descriptions before named pins (FORGE-887).
        ctx = _context(config)
        measured_doc = head.state.get("measured") or {}
        drawn_lengths = (
            wiring_outputs.measured_lengths(measured_doc.get("measured"))
            if measured_doc.get("revision") == head.revision
            else {}
        )
        pin_params = [
            CoupledParameter(owner="electronics", basis=ValueBasis.ESTIMATED, **spec)
            for spec in wiring_outputs.pin_parameter_specs(ctx)
        ]
        names = {x.name for x in pin_params}
        bus_params = [*pin_params, *(x for x in bus_params if x.name not in names)]
        contracts = []
        if cad_params:
            contracts.append(
                InterfaceContract(
                    kind=ContractKind.CAD_EDA,
                    title="Enclosure and electronics",
                    plain="Every chosen part must fit inside the enclosure.",
                    owning_domains=["mechanical", "electronics"],
                    parameters=cad_params[:60],
                    rationale=f"Proposed at the Imagine handover ({head.ref}).",
                )
            )
        if bus_params:
            contracts.append(
                InterfaceContract(
                    kind=ContractKind.EDA_FIRMWARE,
                    title="Electronics and firmware",
                    plain=(
                        "The firmware drives each part over the bus the schematic gives it"
                        + (", on the pins the wiring names." if pin_params else ".")
                    ),
                    owning_domains=["electronics", "firmware"],
                    parameters=bus_params[:60],
                    rationale=f"Proposed at the Imagine handover ({head.ref}).",
                )
            )
        for contract in contracts:
            existing = await get_contract(self.twin, project_id, _key(contract))
            if existing is not None:
                created.append(existing.ref)
                continue
            created.append(await self.bridge.write_contract(project_id, contract, actor=actor))

        # The wiring: table, harness BOM lines and the numbered sequence (FORGE-887).
        wiring = wiring_outputs.handover_record(
            ctx, str(product.get("name") or "the product"), drawn_lengths
        )
        if wiring is not None:
            w_ref, _ = await self.bridge.record_decision(
                project_id, title=wiring[0], rationale=wiring[1], depends_on=[head.ref]
            )
            created.append(w_ref)
            head.state["wiring"] = {
                "decision": w_ref,
                "harness": wiring_outputs.harness_lines(ctx, drawn_lengths),
                "pins": len(pin_params),
                # the controller's signal pins in the firmware agent's pin_map shape
                "firmware_pin_map": wiring_outputs.firmware_pin_map(ctx),
                # the interconnect circuit an agent hands to kicad.generate_schematic
                "circuit": wiring_outputs.interconnect_circuit(
                    ctx, str(product.get("name") or "product")
                ),
            }

        for ev in head.state["events"].values():
            if ev.get("kind") == "screening":
                created += [r for r in ev.get("mapped_to") or [] if r not in created]
        return created

    # -- rework -----------------------------------------------------------

    async def rework(
        self,
        project_id: str,
        op_id: str,
        reason: str,
        *,
        actor: str,
        agent: str | None = None,
        on_behalf_of: str | None = None,
    ) -> StudioResponse:
        with tracer.start_as_current_span("imagine.rework") as span:
            span.set_attribute("project_id", project_id)
            delegated = _delegation("rework", agent, on_behalf_of)
            if delegated:
                actor = delegated["by"]
                span.set_attribute("imagine.agent", str(agent))
            async with self._lock(project_id):
                head = await self._head(project_id)
                if head is None:
                    _count("rework", "refused")
                    raise ImagineError("No studio yet: an agent puts a config first.", 404)
                last = head.state.get("rework") or {}
                if last.get("op_id") == op_id and head.state.get("status") != "handed_over":
                    _count("rework", "duplicate")
                    out = self._response(project_id, head)
                    out.stale = list(last.get("stale") or [])
                    return out
                if head.state.get("status") != "handed_over":
                    _count("rework", "refused")
                    raise ImagineError(
                        f"{head.ref} is still open in Imagine; there is nothing to rework.", 409
                    )
                frozen = head.state.get("handover") or {}
                state = copy.deepcopy(head.state)
                state["status"] = "draft"
                state["handover"] = None
                state["previous_handover"] = frozen
                try:
                    new = await self._write_revision(
                        project_id, state, actor=actor, reason=f"rework: {reason}"
                    )
                    stale = await self._mark_stale(head, new, frozen, reason)
                except ImagineError:
                    raise
                except Exception as exc:
                    span.record_exception(exc)
                    _count("rework", "failed")
                    logger.error("imagine_rework_failed", project_id=project_id, error=str(exc))
                    raise ImagineError("The twin did not take the rework.", 502) from exc
                new.state["rework"] = {
                    "op_id": op_id,
                    "reason": reason,
                    "from_revision": head.revision,
                    "at": _now(),
                    "by": actor,
                    "stale": stale,
                    **delegated,
                }
                await self._save(new)
            _count("rework", "ok")
            self._publish(
                project_id,
                f"rework-{new.revision}",
                (f"Imagine reopened at {new.ref}: {reason}" + _delegated_note(delegated))[:500],
            )
            logger.info(
                "imagine_reworked",
                project_id=project_id,
                ref=new.ref,
                from_ref=head.ref,
                stale=len(stale),
            )
            out = self._response(project_id, new)
            out.stale = stale
            return out

    async def _mark_stale(
        self, old: _Head, new: _Head, frozen: dict[str, Any], reason: str
    ) -> list[str]:
        """The records made for the frozen revision, now stale.

        Moving ``IMG-STUDIO``'s head already marked every record pinned to the
        old revision (decisions carry that pin). Records made from it without
        a pin, such as the screening evidence, are marked here through the
        same staleness path, so Develop and Check see one consistent state.
        """
        from twin_core.consistency.record_pins import record_staleness, set_record_status
        from twin_core.consistency.staleness import StalenessStatus

        refs = list(frozen.get("created") or [])
        for ev in old.state["events"].values():
            refs += [r for r in ev.get("mapped_to") or [] if r not in refs]
        stale: list[str] = []
        why = f"Imagine was reworked ({old.ref} to {new.ref}): {reason}"[:300]
        for ref in refs:
            kind, _, node = ref.partition(":")
            if kind not in ("decision", "evidence") or not node:
                continue
            try:
                node_id = UUID(node)
                wp = await self.twin.graph.get_node(node_id)
            except Exception:  # noqa: BLE001 - a missing or malformed record is skipped
                continue
            if wp is None:
                continue
            meta = getattr(wp, "metadata", None) or {}
            if record_staleness(meta) not in ("current", "revalidated"):
                stale.append(ref)
                continue
            try:
                await set_record_status(self.twin, node_id, StalenessStatus.STALE, why)
                stale.append(ref)
            except Exception as exc:  # noqa: BLE001 - one record never stops a rework
                logger.warning("imagine_mark_stale_failed", ref=ref, error=str(exc))
        return stale

    # -- operations log ---------------------------------------------------

    def _publish(self, project_id: str, what: str, message: str) -> None:
        from api_gateway.operations import publisher as operations

        op = f"job:imagine-{project_id[:8]}-{what}"
        operations.publish(
            project_id,
            op,
            "completed",
            "completed",
            event_id=f"{op}-completed",
            domain="concept",
            title="Imagine",
            message=message[:500],
        )


def _delegation(what: str, agent: str | None, on_behalf_of: str | None) -> dict[str, Any]:
    """The record of an agent deciding a person's step for them (FORGE-824).

    The handover and the rework are a person's decisions. An agent may make
    them only when the gateway owner delegates human-authority decisions
    (``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY``, read here, in the process the
    call lands in), and only naming the person it acts for. The record says
    an agent decided, for that person; it is never written as the person.
    Empty for a person's own call.
    """
    if agent is None:
        return {}
    from api_gateway.approvals.service import (
        AGENT_HUMAN_AUTHORITY_REASON,
        agent_may_decide_human_authority,
    )

    if not agent_may_decide_human_authority():
        _count(what, "refused")
        raise ImagineError(
            f"The Imagine {what} is a person's decision: " + AGENT_HUMAN_AUTHORITY_REASON, 403
        )
    agent = agent.strip()[:100]
    person = (on_behalf_of or "").strip()[:200]
    if not agent or not person:
        _count(what, "refused")
        raise ImagineError(
            f"An agent's Imagine {what} must name the agent and the person it acts for.", 422
        )
    return {"by": f"agent:{agent}", "agent": agent, "on_behalf_of": person}


def _delegated_write(
    kind: str, data: dict[str, Any], previous: dict[str, Any] | None, delegated: dict[str, Any]
) -> dict[str, Any]:
    """An agent's acceptance for a person, as the studio stores it (FORGE-827).

    An assumption the agent confirms or accepts is accepted: it stays an
    assumption, so the manufacturing context keeps it ``assumed``. A scenario
    keeps what the studio already had and is confirmed, marked ``by: agent``.
    Both name the agent and the person.
    """
    who = {"agent": delegated["agent"], "on_behalf_of": delegated["on_behalf_of"]}
    if kind == "assumption_confirmed":
        out = {k: v for k, v in data.items() if k != "confirmed"}
        if data.get("confirmed") is True or data.get("accepted") is True:
            out["accepted"] = True
        return {**out, **who}
    if kind == "scenario":
        return {**(previous or {}), **data, "by": "agent", **who}
    return {**data, **who}


def _agent_label(doc: dict[str, Any]) -> str:
    return f"agent {doc.get('agent')} on behalf of {doc.get('on_behalf_of')}"


def _by_agent_note(by_agent: dict[str, list[str]]) -> str:
    """What the checklist says about items an agent accepted (FORGE-827)."""
    if not by_agent:
        return ""
    parts = [f"{', '.join(ids[:10])} accepted by {who}" for who, ids in by_agent.items()]
    return " (" + "; ".join(parts) + "; not confirmed by a person)"


def _delegated_note(delegated: dict[str, Any]) -> str:
    if not delegated:
        return ""
    return f", decided by agent {delegated['agent']} on behalf of {delegated['on_behalf_of']}"


def _key(contract: Any) -> str:
    from twin_core.models.interface_contract import CONTRACT_KEYS

    return CONTRACT_KEYS[contract.kind]
