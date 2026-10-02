"""Per-role model routing (FORGE-477).

One provider and model used to serve every role, so a flow-generator call that
needs a sentence cost the same as a design phase that drives CAD. This module
maps a *role* to a ``(provider, model)`` route.

Roles: ``flow_generator``, ``phase_brain``, ``phase_brain:<discipline>``,
``gate_check``, ``classification``, ``summarisation``, ``chat``.

Precedence, highest first:

1. an explicit per-call provider/model (the caller said so);
2. the running phase's own ``model`` (flow-generator input I5);
3. the project's routes;
4. the routing table (data file, then ``METAFORGE_ROUTE_<ROLE>`` env);
5. no route: the caller falls back to the durable harness selection.

``phase_brain`` with disciplines tries ``phase_brain:<discipline>`` first, in
the phase's own order, then plain ``phase_brain``.

Every route is checked at load with ``registry.model_family_mismatch``
(FORGE-468). A bad route raises :class:`RoutingConfigError` instead of being
dropped: a route that silently fell back to the durable selection would be the
exact failure FORGE-468 removed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from orchestrator.harness.providers.registry import (
    UnknownProviderError,
    get_profile,
    model_family_mismatch,
)
from orchestrator.harness.providers.usage import current_usage_context

logger = structlog.get_logger(__name__)

__all__ = [
    "BASE_ROLES",
    "ResolvedRoute",
    "Route",
    "RoutingConfigError",
    "RoutingTable",
    "current_routing",
    "effective_routing",
    "load_routing_table",
    "provider_unconfigured",
    "routing_problems",
    "parse_route_ref",
    "resolve_route",
    "routing_scope",
    "routing_table",
    "validate_route_ref",
]

BASE_ROLES: frozenset[str] = frozenset(
    {
        "flow_generator",
        "phase_brain",
        "gate_check",
        "classification",
        "summarisation",
        "chat",
    }
)

_ENV_PREFIX = "METAFORGE_ROUTE_"


class RoutingConfigError(ValueError):
    """A route is malformed, names an unknown provider, or cannot work."""


@dataclass(frozen=True)
class Route:
    provider: str
    model: str

    def ref(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class ResolvedRoute:
    """A route plus where it came from, for logs, provenance and the routing view."""

    route: Route
    role: str
    #: ``call`` | ``phase`` | ``project`` | ``table``
    source: str


def parse_route_ref(ref: str, where: str = "route") -> Route:
    """``"provider:model"`` to a :class:`Route`. Splits on the first colon only,
    so a model id may itself contain one (Bedrock ``...-v1:0``)."""
    provider, sep, model = (ref or "").strip().partition(":")
    if not sep or not provider.strip() or not model.strip():
        raise RoutingConfigError(f"{where}: expected 'provider:model', got {ref!r}")
    return Route(provider=provider.strip().lower(), model=model.strip())


def validate_route_ref(route: Route, where: str) -> Route:
    """Raise :class:`RoutingConfigError` unless the pair can work."""
    try:
        reason = model_family_mismatch(route.provider, route.model)
        canonical = get_profile(route.provider).id
    except UnknownProviderError as exc:
        raise RoutingConfigError(f"{where}: unknown provider '{route.provider}'") from exc
    if reason is not None:
        raise RoutingConfigError(f"{where}: {reason}")
    return Route(provider=canonical, model=route.model)


def _valid_role(role: str) -> bool:
    base, _, discipline = role.partition(":")
    if base not in BASE_ROLES:
        return False
    return not discipline or base == "phase_brain"


def _parse_roles(raw: Any, where: str) -> dict[str, Route]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise RoutingConfigError(f"{where} must be a mapping of role to route")
    out: dict[str, Route] = {}
    for role, entry in raw.items():
        role = str(role).strip().lower()
        if not _valid_role(role):
            raise RoutingConfigError(
                f"{where}['{role}']: unknown role. Known: {sorted(BASE_ROLES)} and "
                "'phase_brain:<discipline>'"
            )
        if isinstance(entry, str):
            route = parse_route_ref(entry, f"{where}['{role}']")
        elif isinstance(entry, Mapping):
            provider, model = entry.get("provider"), entry.get("model")
            if not isinstance(provider, str) or not isinstance(model, str):
                raise RoutingConfigError(f"{where}['{role}'] needs string 'provider' and 'model'")
            route = Route(provider.strip().lower(), model.strip())
        else:
            raise RoutingConfigError(f"{where}['{role}'] must be a route mapping")
        out[role] = validate_route_ref(route, f"{where}['{role}']")
    return out


@dataclass(frozen=True)
class RoutingTable:
    roles: dict[str, Route] = field(default_factory=dict)
    #: project_id to that project's own role routes.
    projects: dict[str, dict[str, Route]] = field(default_factory=dict)

    def lookup(self, role: str, *, project_id: str | None = None) -> tuple[Route, str] | None:
        """The route for exactly ``role``: project first, then the table."""
        if project_id and role in self.projects.get(project_id, {}):
            return self.projects[project_id][role], "project"
        if role in self.roles:
            return self.roles[role], "table"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "roles": {r: {"provider": v.provider, "model": v.model} for r, v in self.roles.items()},
            "projects": {
                p: {r: {"provider": v.provider, "model": v.model} for r, v in rs.items()}
                for p, rs in self.projects.items()
            },
        }


def _env_overrides(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        k[len(_ENV_PREFIX) :].lower().replace("_", "-"): v.strip()
        for k, v in environ.items()
        if k.startswith(_ENV_PREFIX) and v.strip()
    }


def _role_from_env_key(key: str) -> str:
    """``phase-brain-mechanical`` to ``phase_brain:mechanical``."""
    for base in sorted(BASE_ROLES, key=len, reverse=True):
        dashed = base.replace("_", "-")
        if key == dashed:
            return base
        if key.startswith(dashed + "-"):
            return f"{base}:{key[len(dashed) + 1 :]}"
    return key


def load_routing_table(
    data: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None
) -> RoutingTable:
    """Build and validate the table. ``data`` defaults to the data file."""
    env = os.environ if environ is None else environ
    if data is None:
        override = (env.get("METAFORGE_MODEL_ROUTES_PATH") or "").strip()
        path = Path(override) if override else Path(__file__).with_name("model_routes.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RoutingConfigError(f"routing file {path} is unreadable: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RoutingConfigError("routing config must be a mapping")
    roles = _parse_roles(data.get("roles"), "roles")
    for key, ref in _env_overrides(env).items():
        role = _role_from_env_key(key)
        where = f"{_ENV_PREFIX}{key.upper().replace('-', '_')}"
        if not _valid_role(role):
            raise RoutingConfigError(f"{where}: unknown role '{role}'")
        roles[role] = validate_route_ref(parse_route_ref(ref, where), where)
    projects_raw = data.get("projects") or {}
    if not isinstance(projects_raw, Mapping):
        raise RoutingConfigError("'projects' must be a mapping of project id to routes")
    projects = {
        str(pid): _parse_roles((entry or {}).get("roles"), f"projects['{pid}'].roles")
        for pid, entry in projects_raw.items()
    }
    return RoutingTable(roles=roles, projects=projects)


@lru_cache(maxsize=8)
def _cached_table(path: str, mtime: float, env_items: tuple[tuple[str, str], ...]) -> RoutingTable:
    return load_routing_table(None, dict(env_items))


def routing_table() -> RoutingTable:
    """The current table, reloaded when the file or a routing env var changes.

    Raises :class:`RoutingConfigError` when it is bad; call it at startup so a
    bad route refuses to boot instead of failing on the first model call.
    """
    override = (os.environ.get("METAFORGE_MODEL_ROUTES_PATH") or "").strip()
    path = Path(override) if override else Path(__file__).with_name("model_routes.json")
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    env_items = tuple(
        sorted(
            (k, v)
            for k, v in os.environ.items()
            if k.startswith(_ENV_PREFIX) or k == "METAFORGE_MODEL_ROUTES_PATH"
        )
    )
    return _cached_table(str(path), mtime, env_items)


# ------------------------------------------------------------------ scope


@dataclass(frozen=True)
class _RoutingScope:
    project_id: str | None = None
    #: The running phase's own ``provider:model`` (flow input I5).
    phase_model: str | None = None
    disciplines: tuple[str, ...] = ()


_scope: ContextVar[_RoutingScope] = ContextVar("llm_routing_scope", default=_RoutingScope())


def current_routing() -> _RoutingScope:
    return _scope.get()


@contextmanager
def routing_scope(
    *,
    project_id: str | None = None,
    phase_model: str | None = None,
    disciplines: tuple[str, ...] | None = None,
) -> Iterator[None]:
    """Make routing aware of the project and phase a call is made for.

    Fields left ``None`` inherit from the enclosing scope. A phase with no
    model of its own passes ``phase_model=""`` to clear an outer phase's.
    """
    parent = _scope.get()
    merged = _RoutingScope(
        project_id=project_id if project_id is not None else parent.project_id,
        phase_model=(phase_model or None) if phase_model is not None else parent.phase_model,
        disciplines=disciplines if disciplines is not None else parent.disciplines,
    )
    token = _scope.set(merged)
    try:
        yield
    finally:
        _scope.reset(token)


# ---------------------------------------------------------------- resolve


def _role_candidates(role: str, disciplines: tuple[str, ...]) -> list[str]:
    if role == "phase_brain":
        return [*(f"phase_brain:{d}" for d in disciplines), "phase_brain"]
    return [role]


def provider_unconfigured(provider: str) -> str | None:
    """Why ``provider`` has no credentials on this deployment, or ``None``.

    Uses the check behind ``GET /v1/harness/providers`` (stored login, env key,
    Codex auth, keyless local servers). Outside the gateway process (no
    ``api_gateway`` importable) there is nothing to check against, so it passes.
    """
    try:
        from api_gateway.harness.routes import provider_is_configured
    except ImportError:  # pragma: no cover - orchestrator used on its own
        return None
    if provider_is_configured(provider):
        return None
    return f"provider '{provider}' has no credentials on this deployment"


def _require_configured(resolved: ResolvedRoute) -> ResolvedRoute:
    """Refuse a route to a provider with no credentials. Never use it quietly
    (it would 4xx on every call behind the fallback chain) and never skip it
    quietly (the operator asked for that model)."""
    reason = provider_unconfigured(resolved.route.provider)
    if reason is not None:
        raise RoutingConfigError(
            f"route for role '{resolved.role}' ({resolved.route.ref()}, from "
            f"{resolved.source}): {reason}. Log in with `forge auth login`, set its key, "
            "or remove the route."
        )
    return resolved


def routing_problems(project_id: str | None = None) -> list[str]:
    """Every route that would be refused at call time, for health and the routing view."""
    table = routing_table()
    entries: list[tuple[str, str, Route]] = [("table", r, v) for r, v in table.roles.items()]
    for pid, roles in table.projects.items():
        if project_id is None or pid == project_id:
            entries += [(f"project {pid}", r, v) for r, v in roles.items()]
    out = []
    for source, role, route in entries:
        reason = provider_unconfigured(route.provider)
        if reason is not None:
            out.append(f"{source} route '{role}' ({route.ref()}): {reason}")
    return out


def resolve_route(role: str | None = None) -> ResolvedRoute | None:
    """The route for the current call, or ``None`` to use the durable selection.

    ``role`` defaults to the role attributed by FORGE-476's ``usage_scope``.
    Raises :class:`RoutingConfigError` for a bad phase model or table.
    """
    role = role or current_usage_context().role
    if not role:
        return None
    scope = _scope.get()
    if scope.phase_model and role == "phase_brain":
        route = validate_route_ref(parse_route_ref(scope.phase_model, "phase model"), "phase model")
        return _require_configured(ResolvedRoute(route=route, role=role, source="phase"))
    table = routing_table()
    for candidate in _role_candidates(role, scope.disciplines):
        found = table.lookup(candidate, project_id=scope.project_id)
        if found is not None:
            return _require_configured(
                ResolvedRoute(route=found[0], role=candidate, source=found[1])
            )
    return None


def effective_routing(project_id: str | None = None) -> dict[str, Any]:
    """The routing as an operator sees it: the table plus the project's routes."""
    table = routing_table()
    out = table.to_dict()
    out["problems"] = routing_problems(project_id)
    for entry in out["roles"].values():
        entry["configured"] = provider_unconfigured(entry["provider"]) is None
    for roles in out["projects"].values():
        for entry in roles.values():
            entry["configured"] = provider_unconfigured(entry["provider"]) is None
    if project_id:
        out["effective_for_project"] = {
            r: {
                "provider": v.provider,
                "model": v.model,
                "configured": provider_unconfigured(v.provider) is None,
            }
            for r, v in {**table.roles, **table.projects.get(project_id, {})}.items()
        }
    return out
