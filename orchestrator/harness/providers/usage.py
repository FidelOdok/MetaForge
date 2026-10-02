"""Token and cost accounting for every LLM call (FORGE-476).

A model call used to leave no trace of what it cost. This module records one
:class:`UsageEvent` per call: prompt, completion and cached-input tokens, cost,
provider and model, attributed to a run, a phase, a role and a caller.

- **Attribution** travels in a context variable, like ``provenance.py``
  (FORGE-468): whoever starts the work opens :func:`usage_scope` and every
  model call made inside it, including in child tasks, is attributed to it.
- **Unknown is not zero.** A provider that reports no usage records ``None``
  tokens; a model missing from ``model_prices.json`` records ``None`` cost.
  Totals carry ``calls_without_usage`` and ``calls_unpriced`` so a reader can
  tell "nothing was spent" from "nothing was measured". A total's
  ``cost_usd`` is ``None`` when no call in it was priced (FORGE-486), never
  ``0.0``: a zero reads as "free" in a live view and slips under any budget
  check. When only some calls were priced it covers those and
  ``calls_unpriced`` says how many it leaves out.
- **Subscription billing is not zero.** A provider billed by flat
  subscription (Codex via ChatGPT) has no per-call price, so its calls are
  counted in ``calls_subscription`` and the total's ``billing`` says
  ``subscription`` (or ``mixed``), instead of pricing them at 0.
- **Persistence** is a small SQLite file shared by every process that opens
  it, so the gateway and the Temporal design-flow worker write to one place
  and ``GET /v1/runs/{id}`` can read what the worker spent.

Recording is best-effort: it never fails or slows a model call.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from observability.metrics import MetricsCollector

logger = structlog.get_logger(__name__)

__all__ = [
    "ModelPrice",
    "TokenUsage",
    "UsageContext",
    "UsageEvent",
    "UsageStore",
    "configure_usage_store",
    "current_usage_context",
    "default_usage_db_path",
    "ensure_usage_store",
    "get_usage_store",
    "load_prices",
    "price_cost_usd",
    "record_call",
    "run_usage",
    "usage_report",
    "usage_scope",
]

#: Role recorded when a call carries no attribution at all.
UNATTRIBUTED = "unattributed"

#: Providers billed by flat subscription rather than per token (FORGE-486).
SUBSCRIPTION_PROVIDERS = frozenset({"openai-codex", "codex"})


def is_subscription_provider(provider: str) -> bool:
    """True when ``provider`` is billed by subscription, so has no per-call cost."""
    return provider in SUBSCRIPTION_PROVIDERS


# --------------------------------------------------------------------- prices


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for one provider/model pair."""

    input: float
    output: float
    cached_input: float | None = None
    cache_write: float | None = None


def _prices_path() -> Path:
    override = os.environ.get("METAFORGE_MODEL_PRICES_PATH", "").strip()
    return Path(override) if override else Path(__file__).with_name("model_prices.json")


@lru_cache(maxsize=4)
def _load_prices_cached(path: str) -> dict[tuple[str, str], ModelPrice]:
    out: dict[tuple[str, str], ModelPrice] = {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in raw.get("prices", []):
            out[(str(row["provider"]), str(row["model"]))] = ModelPrice(
                input=float(row["input"]),
                output=float(row["output"]),
                cached_input=(
                    float(row["cached_input"]) if row.get("cached_input") is not None else None
                ),
                cache_write=(
                    float(row["cache_write"]) if row.get("cache_write") is not None else None
                ),
            )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # An unreadable table makes every cost unknown, which is loud in the
        # totals (calls_unpriced) rather than silently zero.
        logger.warning("llm_price_table_unreadable", path=path, error=str(exc))
    return out


def load_prices() -> dict[tuple[str, str], ModelPrice]:
    """The per-model price table (``model_prices.json``)."""
    return _load_prices_cached(str(_prices_path()))


# --------------------------------------------------------------------- tokens


@dataclass(frozen=True)
class TokenUsage:
    """Normalised token counts for one call.

    ``prompt`` is the total input including cached tokens, so families are
    comparable: OpenAI already reports it that way, Anthropic reports
    ``input_tokens`` excluding cache reads and writes and is summed here.
    """

    prompt: int
    completion: int
    cached_input: int = 0
    cache_write: int = 0

    @classmethod
    def from_provider(cls, usage: Mapping[str, Any] | None) -> TokenUsage | None:
        """Build from an adapter's ``usage`` dict; ``None`` when none reported."""
        if not usage:
            return None
        try:
            raw_in = int(usage.get("input_tokens", 0) or 0)
            out = int(usage.get("output_tokens", 0) or 0)
            cached = int(usage.get("cached_input_tokens", 0) or 0)
            written = int(usage.get("cache_creation_input_tokens", 0) or 0)
        except (TypeError, ValueError):
            return None
        # Adapters flag families whose input_tokens already includes the
        # cache (OpenAI, Codex); Anthropic's excludes it.
        prompt = raw_in if usage.get("input_includes_cached") else raw_in + cached + written
        return cls(prompt=prompt, completion=out, cached_input=cached, cache_write=written)


def price_cost_usd(provider: str, model: str, tokens: TokenUsage | None) -> float | None:
    """USD cost of ``tokens``, or ``None`` when it cannot be known."""
    if tokens is None or is_subscription_provider(provider):
        return None
    price = load_prices().get((provider, model))
    if price is None:
        return None
    cached_rate = price.cached_input if price.cached_input is not None else price.input
    write_rate = price.cache_write if price.cache_write is not None else price.input
    fresh = max(tokens.prompt - tokens.cached_input - tokens.cache_write, 0)
    return (
        fresh * price.input
        + tokens.cached_input * cached_rate
        + tokens.cache_write * write_rate
        + tokens.completion * price.output
    ) / 1_000_000


# ---------------------------------------------------------------- attribution


@dataclass(frozen=True)
class UsageContext:
    """Who a model call is for."""

    run_id: str | None = None
    phase: str | None = None
    #: What the call is doing: flow_generator, phase_brain, gate_check, chat...
    role: str | None = None
    #: The client or surface that asked (cli, dashboard, mcp client name...).
    caller: str | None = None


_ctx: ContextVar[UsageContext] = ContextVar("llm_usage_context", default=UsageContext())


def current_usage_context() -> UsageContext:
    return _ctx.get()


@contextmanager
def usage_scope(
    *,
    run_id: str | None = None,
    phase: str | None = None,
    role: str | None = None,
    caller: str | None = None,
    default_role: str | None = None,
) -> Iterator[UsageContext]:
    """Attribute every model call inside the block.

    Fields left ``None`` inherit from the enclosing scope, so a phase scope can
    set only ``phase`` under a run scope that set ``run_id``. ``default_role``
    applies only when neither this scope nor an enclosing one set a role, so an
    entry point can say "chat" without overriding a caller's "phase_brain".
    """
    parent = _ctx.get()
    merged = UsageContext(
        run_id=run_id if run_id is not None else parent.run_id,
        phase=phase if phase is not None else parent.phase,
        role=role if role is not None else (parent.role or default_role),
        caller=caller if caller is not None else parent.caller,
    )
    token = _ctx.set(merged)
    try:
        yield merged
    finally:
        _ctx.reset(token)


# ---------------------------------------------------------------------- store


@dataclass(frozen=True)
class UsageEvent:
    ts: float
    run_id: str | None
    phase: str | None
    role: str
    caller: str | None
    provider: str
    model: str
    #: ``None`` means the provider did not report usage, not that it was zero.
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_input_tokens: int | None
    #: ``None`` means unknown (no usage, or the model has no price).
    cost_usd: float | None


@dataclass
class _Bucket:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_input_tokens: int = 0
    #: Sum over priced calls only; reported as ``None`` while none were priced.
    cost_usd: float = 0.0
    calls_priced: int = 0
    calls_without_usage: int = 0
    calls_unpriced: int = 0
    calls_subscription: int = 0

    def add(self, e: UsageEvent) -> None:
        self.calls += 1
        if e.prompt_tokens is None and e.completion_tokens is None:
            self.calls_without_usage += 1
        self.prompt_tokens += e.prompt_tokens or 0
        self.completion_tokens += e.completion_tokens or 0
        self.cached_input_tokens += e.cached_input_tokens or 0
        if e.cost_usd is not None:
            self.calls_priced += 1
            self.cost_usd += e.cost_usd
        elif is_subscription_provider(e.provider):
            self.calls_subscription += 1
        else:
            self.calls_unpriced += 1

    @property
    def billing(self) -> str:
        """``subscription``, ``metered`` or ``mixed`` across the calls in this bucket."""
        if self.calls_subscription == 0:
            return "metered"
        return "subscription" if self.calls_subscription == self.calls else "mixed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            # None, not 0.0, when no call was priced; a lower bound while any
            # call was unpriced.
            "cost_usd": round(self.cost_usd, 6) if self.calls_priced else None,
            "calls_unpriced": self.calls_unpriced,
            "calls_subscription": self.calls_subscription,
            "billing": self.billing,
            "calls_without_usage": self.calls_without_usage,
        }


def _summarise(events: list[UsageEvent]) -> dict[str, Any]:
    total = _Bucket()
    by_phase: dict[str, _Bucket] = defaultdict(_Bucket)
    by_role: dict[str, _Bucket] = defaultdict(_Bucket)
    by_model: dict[str, _Bucket] = defaultdict(_Bucket)
    for e in events:
        total.add(e)
        by_phase[e.phase or "(none)"].add(e)
        by_role[e.role].add(e)
        by_model[f"{e.provider}:{e.model}"].add(e)
    return {
        **total.to_dict(),
        "by_phase": {k: v.to_dict() for k, v in by_phase.items()},
        "by_role": {k: v.to_dict() for k, v in by_role.items()},
        "by_model": {k: v.to_dict() for k, v in by_model.items()},
    }


def default_usage_db_path() -> Path:
    """``METAFORGE_LLM_USAGE_DB_PATH``, else ``llm_usage.db`` beside the run ledger."""
    override = os.environ.get("METAFORGE_LLM_USAGE_DB_PATH", "").strip()
    if override:
        return Path(override)
    from orchestrator.harness.ledger import default_ledger_path

    return default_ledger_path().with_name("llm_usage.db")


def _spend_alert_threshold() -> float:
    try:
        return float(os.environ.get("METAFORGE_RUN_SPEND_ALERT_USD", "5"))
    except ValueError:
        return 5.0


class UsageStore:
    """SQLite-backed usage events; ``:memory:`` for tests."""

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    run_id TEXT,
                    phase TEXT,
                    role TEXT NOT NULL,
                    caller TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    cached_input_tokens INTEGER,
                    cost_usd REAL
                );
                CREATE INDEX IF NOT EXISTS idx_llm_usage_run ON llm_usage(run_id);
                CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts);
                """
            )
            self._conn.commit()
        self._alerted: set[str] = set()

    def add(self, e: UsageEvent) -> float | None:
        """Persist ``e``; returns the run's accumulated priced cost.

        ``None`` when the event has no run or no call of the run is priced, so
        the spend alert never reads unknown as zero.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_usage (ts, run_id, phase, role, caller, provider, model,"
                " prompt_tokens, completion_tokens, cached_input_tokens, cost_usd)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    e.ts,
                    e.run_id,
                    e.phase,
                    e.role,
                    e.caller,
                    e.provider,
                    e.model,
                    e.prompt_tokens,
                    e.completion_tokens,
                    e.cached_input_tokens,
                    e.cost_usd,
                ),
            )
            self._conn.commit()
            if e.run_id is None:
                return None
            row = self._conn.execute(
                "SELECT SUM(cost_usd) FROM llm_usage WHERE run_id = ?", (e.run_id,)
            ).fetchone()
        return float(row[0]) if row[0] is not None else None

    def _events(self, where: str, args: tuple[Any, ...]) -> list[UsageEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, run_id, phase, role, caller, provider, model, prompt_tokens,"
                f" completion_tokens, cached_input_tokens, cost_usd FROM llm_usage {where}"
                " ORDER BY id",
                args,
            ).fetchall()
        return [UsageEvent(*r) for r in rows]

    def run_totals(self, run_id: str) -> dict[str, Any] | None:
        """Totals for a run overall and per phase, role and model; ``None`` if none."""
        events = self._events("WHERE run_id = ?", (run_id,))
        return _summarise(events) if events else None

    def summary_since(self, seconds: float, *, now: float | None = None) -> dict[str, Any]:
        """Totals across all runs for the trailing ``seconds`` (24 h in health.check)."""
        since = (now if now is not None else time.time()) - seconds
        out = _summarise(self._events("WHERE ts >= ?", (since,)))
        out["window_seconds"] = seconds
        return out

    def should_alert(self, run_id: str, total: float | None) -> bool:
        """True once per run, at the moment its spend crosses the threshold.

        An unknown total (``None``: nothing priced) never alerts and is not
        treated as 0 in either direction; ``calls_unpriced`` is the signal.
        """
        if total is None or total < _spend_alert_threshold():
            return False
        with self._lock:
            if run_id in self._alerted:
                return False
            self._alerted.add(run_id)
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def run_usage(run_id: str) -> dict[str, Any] | None:
    """Totals for a run from the shared store, or ``None`` if none are recorded.

    ``None`` is "no usage events", which for a run that made model calls means
    accounting was not reachable, not that nothing was spent.
    """
    store = ensure_usage_store()
    if store is None:
        return None
    try:
        return store.run_totals(run_id)
    except sqlite3.Error as exc:
        logger.warning("llm_usage_read_failed", run_id=run_id, error=str(exc))
        return None


def usage_report(window_seconds: float = 86400.0) -> dict[str, Any]:
    """Trailing-window summary for ``health.check``; never raises."""
    store = ensure_usage_store()
    if store is None:
        return {"available": False, "reason": "usage store could not be opened"}
    try:
        return {"available": True, **store.summary_since(window_seconds)}
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"usage store unreadable: {exc}"}


_store: UsageStore | None = None
_store_lock = threading.Lock()


def configure_usage_store(store: UsageStore | None) -> None:
    """Install (or, with ``None``, remove) the process-wide store."""
    global _store  # noqa: PLW0603
    with _store_lock:
        _store = store


def get_usage_store() -> UsageStore | None:
    return _store


def ensure_usage_store() -> UsageStore | None:
    """Open the default shared store once; the gateway and the worker call this."""
    global _store  # noqa: PLW0603
    with _store_lock:
        if _store is None:
            try:
                _store = UsageStore(str(default_usage_db_path()))
            except (OSError, sqlite3.Error) as exc:
                logger.warning("llm_usage_store_unavailable", error=str(exc))
        return _store


# ------------------------------------------------------------------ recording


def record_call(
    *,
    provider: str,
    model: str,
    pipeline_role: str,
    usage: Mapping[str, Any] | None,
    metrics: MetricsCollector | None = None,
    reported: bool = True,
) -> UsageEvent | None:
    """Record one model call against the current :class:`UsageContext`.

    ``usage`` is the adapter's usage dict. ``reported=False`` records the call
    with unknown tokens (a streaming path that cannot see usage). Never raises.
    """
    try:
        ctx = _ctx.get()
        tokens = TokenUsage.from_provider(usage) if reported else None
        # The attributed role (phase_brain, flow_generator...) wins over the
        # pipeline's slot name (generator, planner...), which only says which
        # model slot answered.
        role = ctx.role or pipeline_role or UNATTRIBUTED
        event = UsageEvent(
            ts=time.time(),
            run_id=ctx.run_id,
            phase=ctx.phase,
            role=role,
            caller=ctx.caller,
            provider=provider,
            model=model,
            prompt_tokens=tokens.prompt if tokens else None,
            completion_tokens=tokens.completion if tokens else None,
            cached_input_tokens=tokens.cached_input if tokens else None,
            cost_usd=price_cost_usd(provider, model, tokens),
        )
        logger.info(
            "llm_usage",
            run_id=event.run_id,
            phase=event.phase,
            role=event.role,
            caller=event.caller,
            provider=provider,
            model=model,
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            cached_input_tokens=event.cached_input_tokens,
            cost_usd=event.cost_usd,
        )
        if metrics is not None:
            metrics.record_llm_usage(
                role=role,
                provider=provider,
                model=model,
                prompt=event.prompt_tokens,
                completion=event.completion_tokens,
                cached_input=event.cached_input_tokens,
                cost_usd=event.cost_usd,
            )
        store = _store
        if store is not None:
            run_total = store.add(event)
            if (
                run_total is not None
                and event.run_id is not None
                and store.should_alert(event.run_id, run_total)
            ):
                logger.warning(
                    "llm_run_spend_exceeded",
                    run_id=event.run_id,
                    cost_usd=round(run_total, 4),
                    threshold_usd=_spend_alert_threshold(),
                )
                if metrics is not None:
                    metrics.record_llm_run_spend_exceeded(role)
        return event
    except Exception as exc:  # noqa: BLE001 - accounting must never break a model call
        logger.warning("llm_usage_record_failed", error=str(exc))
        return None
