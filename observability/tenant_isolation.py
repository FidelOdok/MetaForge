"""Per-tenant metric isolation for MetaForge multi-tenant observability.

Provides tenant context injection, RBAC for metric visibility, and
Grafana/Alertmanager integration helpers. Each tenant's metrics are
labelled with ``tenant_id`` so dashboards and alerts can be scoped
accordingly.

MET-123
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Tenant context model
# ---------------------------------------------------------------------------

_ADMIN_TENANT_ID = "__admin__"


class TenantContext(BaseModel):
    """Identity and plan metadata for a single tenant."""

    tenant_id: str
    tenant_name: str
    plan: str  # e.g. "free", "pro", "enterprise"

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("tenant_id must be a non-empty string")
        return v

    @field_validator("tenant_name")
    @classmethod
    def _validate_tenant_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("tenant_name must be a non-empty string")
        return v

    @field_validator("plan")
    @classmethod
    def _validate_plan(cls, v: str) -> str:
        allowed = {"free", "pro", "enterprise"}
        if v not in allowed:
            raise ValueError(f"plan must be one of {allowed}, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Metric label injection
# ---------------------------------------------------------------------------


class TenantMetricInjector:
    """Injects tenant labels into metric attribute dicts and produces
    Grafana / Alertmanager filter expressions."""

    @staticmethod
    def inject_tenant_labels(attributes: dict, tenant: TenantContext) -> dict:
        """Return a *new* dict with ``tenant_id`` added to *attributes*."""
        result = dict(attributes)
        result["tenant_id"] = tenant.tenant_id
        return result

    @staticmethod
    def get_tenant_dashboard_filter(tenant_id: str) -> str:
        """Return a Grafana variable filter expression for *tenant_id*.

        Example return: ``tenant_id="acme-corp"``
        """
        return f'tenant_id="{tenant_id}"'

    @staticmethod
    def get_tenant_alert_matcher(tenant_id: str) -> dict:
        """Return an Alertmanager matcher dict for *tenant_id*.

        Returns a dict suitable for inclusion in Alertmanager routing
        configuration, e.g. ``{"tenant_id": "acme-corp"}``.
        """
        return {"tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# Tenant RBAC for metric visibility
# ---------------------------------------------------------------------------


class TenantRBAC:
    """Role-based access control for per-tenant metric visibility."""

    @staticmethod
    def can_view_metrics(viewer_tenant_id: str, metric_tenant_id: str) -> bool:
        """Return ``True`` if *viewer_tenant_id* may view metrics belonging
        to *metric_tenant_id*.

        Rules:
        - Admin (``__admin__``) can view all tenants.
        - A tenant can view only its own metrics.
        """
        if viewer_tenant_id == _ADMIN_TENANT_ID:
            return True
        return viewer_tenant_id == metric_tenant_id

    @staticmethod
    def get_visible_tenants(
        viewer_tenant_id: str,
        is_admin: bool,
        all_tenant_ids: list[str] | None = None,
    ) -> list[str]:
        """Return the list of tenant IDs that *viewer_tenant_id* can see.

        Parameters
        ----------
        viewer_tenant_id:
            The tenant performing the query.
        is_admin:
            Whether the viewer has admin privileges.
        all_tenant_ids:
            The full set of known tenant IDs. Required when *is_admin*
            is ``True`` so we can return all of them.
        """
        if is_admin:
            return list(all_tenant_ids) if all_tenant_ids else []
        return [viewer_tenant_id]
