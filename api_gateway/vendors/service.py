"""Quotes and orders across vendor connectors, recorded in the twin (FORGE-427).

``quote``
    Ask one connector to price a request. The quote is kept in the vendor
    store and recorded in the twin as a procurement record, so every price
    MetaForge was given is on file with its vendor, mode and date (the start
    of FORGE-460's price history). Additive.

``place_order``
    Turn a stored quote into an order. Refused when the quote has expired or
    the connector's mode has changed since it was made. The order is recorded
    in the twin linked to its quote. A production order spends money, so the
    MCP tool is destructive and held for the person on every call.

``order_status``
    Ask the vendor where the order is. Reads only.

Nothing here decides to buy; a person approves each order.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from api_gateway.vendors.connectors import FakeVendorConnector, VendorConnector, VendorError
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.vendors.service")

__all__ = [
    "SqliteVendorStore",
    "VendorServiceError",
    "connector_report",
    "default_vendor_store_path",
    "get_connectors",
    "get_vendor_store",
    "init_connectors",
    "init_vendor_store",
    "order_status",
    "place_order",
    "quote",
]


class VendorServiceError(ValueError):
    """Not a vendor failure: the request cannot go ahead as asked."""


def default_vendor_store_path() -> Path:
    override = os.environ.get("METAFORGE_VENDOR_STORE_PATH", "").strip()
    return Path(override) if override else Path.home() / ".metaforge" / "vendors.db"


class SqliteVendorStore:
    """Quotes and orders, each a JSON body keyed by a local id."""

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vendor_records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "body TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        self._conn.commit()

    def put(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        record = {**body, "id": f"{kind[0]}_{uuid.uuid4().hex[:12]}", "created_at": time.time()}
        with self._lock:
            self._conn.execute(
                "INSERT INTO vendor_records (id, kind, body, created_at) VALUES (?, ?, ?, ?)",
                (record["id"], kind, json.dumps(record), record["created_at"]),
            )
            self._conn.commit()
        return record

    def get(self, record_id: str, kind: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM vendor_records WHERE id = ? AND kind = ?", (record_id, kind)
            ).fetchone()
        if row is None:
            raise VendorServiceError(f"no {kind} '{record_id}'")
        body: dict[str, Any] = json.loads(row[0])
        return body

    def update(self, record_id: str, kind: str, changes: dict[str, Any]) -> dict[str, Any]:
        body = {**self.get(record_id, kind), **changes}
        with self._lock:
            self._conn.execute(
                "UPDATE vendor_records SET body = ? WHERE id = ?", (json.dumps(body), record_id)
            )
            self._conn.commit()
        return body


_store: SqliteVendorStore | None = None
_connectors: dict[str, VendorConnector] | None = None


def get_vendor_store() -> SqliteVendorStore:
    global _store  # noqa: PLW0603
    if _store is None:
        _store = SqliteVendorStore()
    return _store


def init_vendor_store(store: SqliteVendorStore | None) -> None:
    global _store  # noqa: PLW0603
    _store = store


def _configured_connectors() -> dict[str, VendorConnector]:
    """The connectors this deployment has. Real vendors are added here as they land."""
    out: dict[str, VendorConnector] = {}
    if (os.environ.get("METAFORGE_VENDOR_FAKE") or "").strip().lower() in ("1", "on", "true"):
        out["fake"] = FakeVendorConnector()
    return out


def get_connectors() -> dict[str, VendorConnector]:
    global _connectors  # noqa: PLW0603
    if _connectors is None:
        _connectors = _configured_connectors()
    return _connectors


def init_connectors(connectors: dict[str, VendorConnector] | None) -> None:
    """Install connectors (tests), or ``None`` to re-read the environment."""
    global _connectors  # noqa: PLW0603
    _connectors = connectors


def _connector(connector_id: str) -> VendorConnector:
    connector = get_connectors().get(connector_id)
    if connector is None:
        configured = ", ".join(sorted(get_connectors())) or "none"
        raise VendorServiceError(
            f"no vendor connector '{connector_id}' on this gateway (configured: {configured})"
        )
    return connector


async def connector_report() -> list[dict[str, Any]]:
    """Every connector with its services, mode and live auth state."""
    out = []
    for connector in get_connectors().values():
        status = await connector.auth_status()
        out.append({**status, "services": list(connector.services)})
    return out


async def _record(kind: str, body: dict[str, Any], source_node_ids: list[str]) -> str | None:
    """Write the quote or order to the twin as a procurement record."""
    from api_gateway.projects.routes import get_project_backend
    from api_gateway.twin.routes import get_twin
    from api_gateway.twin.structured_document_recorder import make_procurement_record_recorder

    commit = make_procurement_record_recorder(get_twin(), get_project_backend())
    lines = [
        {**line, "distributor": body["connector_name"], "currency": body["currency"]}
        for line in body["lines"]
    ]
    notes = (
        f"{kind.title()} from {body['connector_name']} ({body['mode']} mode). "
        f"Vendor reference {body.get('vendor_order_ref') or body.get('vendor_ref')}. "
        + (
            f"Valid until {time.strftime('%Y-%m-%d', time.gmtime(body['valid_until']))}. "
            if body.get("valid_until")
            else ""
        )
        + ("A sandbox record: nothing was bought." if body["mode"] == "sandbox" else "")
    )
    result = await commit(
        name=f"{kind.title()} {body['id']} ({body['connector']})",
        line_items=lines,
        notes=notes,
        source_node_ids=source_node_ids,
        project_id=body.get("project_id"),
        source_tool=f"vendor.{'quote' if kind == 'quote' else 'place_order'}",
    )
    node_id = result.get("node_id")
    return str(node_id) if node_id else None


async def quote(request: dict[str, Any], *, by: str = "") -> dict[str, Any]:
    connector = _connector(str(request.get("connector_id") or ""))
    with tracer.start_as_current_span("vendor.quote") as span:
        span.set_attribute("vendor.connector", connector.id)
        span.set_attribute("vendor.mode", connector.mode)
        try:
            priced = await connector.quote(request)
        except VendorError as exc:
            span.record_exception(exc)
            logger.warning("vendor_quote_failed", connector=connector.id, kind=exc.kind)
            raise
    lines = priced["lines"]
    total = sum(float(ln["quantity"]) * float(ln["unit_cost"]) for ln in lines) + float(
        priced.get("shipping") or 0
    )
    body = get_vendor_store().put(
        "quote",
        {
            **priced,
            "connector_name": connector.name,
            "service": request.get("service"),
            "request": request,
            "total": round(total, 2),
            "project_id": request.get("project_id"),
            "by": by,
        },
    )
    node = await _record("quote", body, [])
    body = get_vendor_store().update(body["id"], "quote", {"twin_node_id": node})
    logger.info("vendor_quoted", quote_id=body["id"], connector=connector.id, total=body["total"])
    return {"quote": body}


async def place_order(quote_id: str, *, by: str = "") -> dict[str, Any]:
    store = get_vendor_store()
    held = store.get(quote_id, "quote")
    if held.get("order_id"):
        raise VendorServiceError(f"quote {quote_id} was already ordered as {held['order_id']}")
    if float(held.get("valid_until") or 0) < time.time():
        raise VendorServiceError(f"quote {quote_id} has expired; quote again")
    connector = _connector(str(held["connector"]))
    with tracer.start_as_current_span("vendor.place_order") as span:
        span.set_attribute("vendor.connector", connector.id)
        span.set_attribute("vendor.mode", connector.mode)
        placed = await connector.place_order(held)
    order = store.put(
        "order",
        {
            "quote_id": quote_id,
            "connector": connector.id,
            "connector_name": connector.name,
            "mode": placed["mode"],
            "vendor_order_ref": placed["vendor_order_ref"],
            "state": placed.get("state", "placed"),
            "lines": held["lines"],
            "currency": held["currency"],
            "total": held["total"],
            "project_id": held.get("project_id"),
            "by": by,
        },
    )
    sources = [held["twin_node_id"]] if held.get("twin_node_id") else []
    node = await _record("order", order, sources)
    order = store.update(order["id"], "order", {"twin_node_id": node})
    store.update(quote_id, "quote", {"order_id": order["id"]})
    logger.info(
        "vendor_order_placed",
        order_id=order["id"],
        connector=connector.id,
        mode=order["mode"],
        total=order["total"],
        by=by,
    )
    return {"order": order}


async def order_status(order_id: str) -> dict[str, Any]:
    order = get_vendor_store().get(order_id, "order")
    connector = _connector(str(order["connector"]))
    status = await connector.order_status(str(order["vendor_order_ref"]))
    return {"order": order, "status": status}
