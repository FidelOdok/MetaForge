"""The shared base every vendor connector is built on (FORGE-427).

A vendor connector does four things: say whether its credential works
(``auth_status``), price a request (``quote``), turn a quote into an order
(``place_order``) and report an order (``order_status``). Everything a real
vendor (JLCPCB, PCBWay, a distributor) adds is behind those four.

What the base guarantees, so each vendor does not have to remember:

* **Loud failures.** Every failure is a :class:`VendorError` with a ``kind``
  (``auth``, ``unreachable``, ``rate_limited``, ``refused``, ``protocol``,
  ``config``, ``not_supported``). A connector never answers an empty success.
* **Named credentials.** A key is read from a ``METAFORGE_VENDOR_*``
  environment variable on the gateway, never stored or passed by a caller.
* **An explicit mode.** ``sandbox`` or ``production``, from
  ``METAFORGE_VENDOR_<ID>_MODE``; sandbox unless production is set, and
  reported everywhere a quote or order is shown. A sandbox order spends
  nothing; a production order spends money.
* **A rate limit.** Calls closer together than ``min_interval_seconds`` wait.

Placing an order is the held write; the MCP annotation does that, not this
module. See ``api_gateway.vendors.service``.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

MODES = ("sandbox", "production")
CREDENTIAL_ENV_PREFIX = "METAFORGE_VENDOR_"
#: What a connector can make or supply; a quote names one.
SERVICES = (
    "pcb_fabrication",
    "pcb_assembly",
    "stencil",
    "additive",
    "cnc",
    "sheet_metal",
    "laser_cutting",
    "moulding",
    "components",
)
#: Normalised order states across vendors.
ORDER_STATES = (
    "placed",
    "in_review",
    "in_production",
    "shipped",
    "delivered",
    "cancelled",
    "failed",
)

__all__ = [
    "CREDENTIAL_ENV_PREFIX",
    "MODES",
    "ORDER_STATES",
    "SERVICES",
    "FakeVendorConnector",
    "VendorConnector",
    "VendorError",
]


class VendorError(RuntimeError):
    """A vendor call failed. ``kind`` says why; the message says what to do."""

    KINDS = (
        "auth",
        "unreachable",
        "rate_limited",
        "refused",
        "protocol",
        "config",
        "not_supported",
    )

    def __init__(self, kind: str, message: str) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"unknown vendor error kind {kind!r}")
        self.kind = kind
        super().__init__(message)


class VendorConnector(ABC):
    """Subclass per vendor; implement the four ``_do_*`` methods."""

    #: Short id, used in tool calls and env names ("jlcpcb").
    id: str = ""
    name: str = ""
    services: tuple[str, ...] = ()
    #: Modes the vendor actually offers; a vendor with no sandbox lists only
    #: production, and then the mode must be set to production on purpose.
    supported_modes: tuple[str, ...] = MODES
    min_interval_seconds: float = 0.0

    def __init__(
        self,
        *,
        mode: str | None = None,
        environ: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        env = os.environ if environ is None else environ
        chosen = mode or env.get(f"{CREDENTIAL_ENV_PREFIX}{self.id.upper()}_MODE") or "sandbox"
        chosen = chosen.strip().lower()
        if chosen not in MODES:
            raise VendorError("config", f"{self.id}: mode must be sandbox or production")
        self.mode = chosen
        self._env = env
        self._clock = clock
        self._last_call: float | None = None
        self._lock = asyncio.Lock()

    # ── what a subclass provides ────────────────────────────────────────

    @property
    def credential_env(self) -> str:
        """The env var holding the key; empty for a vendor that needs none."""
        return f"{CREDENTIAL_ENV_PREFIX}{self.id.upper()}_KEY"

    @abstractmethod
    async def _do_auth_status(self, key: str) -> None:
        """Raise ``VendorError("auth")`` if the key is refused."""

    @abstractmethod
    async def _do_quote(self, key: str, request: dict[str, Any]) -> dict[str, Any]:
        """Return ``{vendor_ref, currency, lines, shipping, valid_until, endpoint}``."""

    @abstractmethod
    async def _do_place_order(self, key: str, quote: dict[str, Any]) -> dict[str, Any]:
        """Return ``{vendor_order_ref, state}``."""

    @abstractmethod
    async def _do_order_status(self, key: str, vendor_order_ref: str) -> dict[str, Any]:
        """Return ``{state, tracking?, detail?}``."""

    # ── what the base does around them ──────────────────────────────────

    def _key(self) -> str:
        env = self.credential_env
        if not env:
            return ""
        key = (self._env.get(env) or "").strip()
        if not key:
            raise VendorError("config", f"{env} is not set on the gateway")
        return key

    async def _paced(self) -> None:
        async with self._lock:
            if self._last_call is not None and self.min_interval_seconds > 0:
                wait = self.min_interval_seconds - (self._clock() - self._last_call)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_call = self._clock()

    def _check_mode(self) -> None:
        if self.mode not in self.supported_modes:
            raise VendorError(
                "config",
                f"{self.name} offers no {self.mode} mode; set "
                f"{CREDENTIAL_ENV_PREFIX}{self.id.upper()}_MODE to one of "
                f"{', '.join(self.supported_modes)}",
            )

    async def auth_status(self) -> dict[str, Any]:
        """Never raises: the state is the answer (for ``health.check``)."""
        base = {"connector": self.id, "name": self.name, "mode": self.mode}
        try:
            self._check_mode()
            key = self._key()
            await self._paced()
            await self._do_auth_status(key)
        except VendorError as exc:
            state = {"config": "missing", "auth": "refused"}.get(exc.kind, exc.kind)
            return {**base, "auth": state, "detail": str(exc)}
        return {**base, "auth": "ok"}

    async def quote(self, request: dict[str, Any]) -> dict[str, Any]:
        service = request.get("service")
        if service not in self.services:
            raise VendorError(
                "not_supported",
                f"{self.name} does not quote {service}; it offers {', '.join(self.services)}",
            )
        self._check_mode()
        key = self._key()
        await self._paced()
        result = await self._do_quote(key, request)
        lines = result.get("lines") or []
        if not lines or any("unit_cost" not in ln or "quantity" not in ln for ln in lines):
            raise VendorError("protocol", f"{self.name} returned a quote with no priced lines")
        return {**result, "mode": self.mode, "connector": self.id}

    async def place_order(self, quote: dict[str, Any]) -> dict[str, Any]:
        self._check_mode()
        if quote.get("mode") != self.mode:
            raise VendorError(
                "refused",
                f"the quote was made in {quote.get('mode')} mode and {self.name} is now in "
                f"{self.mode} mode; quote again",
            )
        key = self._key()
        await self._paced()
        result = await self._do_place_order(key, quote)
        if not result.get("vendor_order_ref"):
            raise VendorError("protocol", f"{self.name} accepted the order but gave no reference")
        return {**result, "mode": self.mode}

    async def order_status(self, vendor_order_ref: str) -> dict[str, Any]:
        key = self._key()
        await self._paced()
        result = await self._do_order_status(key, vendor_order_ref)
        if result.get("state") not in ORDER_STATES:
            raise VendorError("protocol", f"{self.name} reported an unknown order state")
        return result


class FakeVendorConnector(VendorConnector):
    """A deterministic in-process vendor for tests and local development.

    Sandbox only. Prices are fixed and obviously fake so nobody mistakes one
    for a real figure. Enabled on a server only with ``METAFORGE_VENDOR_FAKE=on``.
    """

    id = "fake"
    name = "Fake Vendor (test only)"
    services = ("pcb_fabrication", "additive", "cnc")
    supported_modes = ("sandbox",)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.orders: dict[str, dict[str, Any]] = {}
        self.refuse_key = False

    @property
    def credential_env(self) -> str:
        return ""

    async def _do_auth_status(self, key: str) -> None:
        if self.refuse_key:
            raise VendorError("auth", "the fake vendor was told to refuse the key")

    async def _do_quote(self, key: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.refuse_key:
            raise VendorError("auth", "the fake vendor was told to refuse the key")
        quantity = int(request.get("quantity") or 1)
        lines = [
            {
                "part_number": str(
                    item.get("work_product_id") or item.get("part_number") or "item"
                ),
                "description": f"{request['service']} (fake price)",
                "quantity": quantity * int(item.get("quantity") or 1),
                "unit_cost": 1.0,
                "currency": "USD",
                "lead_time_days": 7,
            }
            for item in request.get("items") or [{}]
        ]
        return {
            "vendor_ref": f"fakeq_{uuid.uuid4().hex[:8]}",
            "currency": "USD",
            "lines": lines,
            "shipping": 0.0,
            "valid_until": time.time() + 7 * 86400,
            "endpoint": "fake://quote",
        }

    async def _do_place_order(self, key: str, quote: dict[str, Any]) -> dict[str, Any]:
        ref = f"fakeo_{uuid.uuid4().hex[:8]}"
        self.orders[ref] = {"state": "placed"}
        return {"vendor_order_ref": ref, "state": "placed"}

    async def _do_order_status(self, key: str, vendor_order_ref: str) -> dict[str, Any]:
        order = self.orders.get(vendor_order_ref)
        if order is None:
            raise VendorError("refused", f"no order {vendor_order_ref} at the fake vendor")
        return dict(order)
