"""Client-side configuration + interactive wizard for the forge CLI (MET-555).

Stores *your* preferences — which gateway to talk to, and the default
provider/model/mode to use — in ``~/.forge/config.json`` (override with
``FORGE_CONFIG``). ``forge chat`` reads these so you don't retype flags.

Scope: this configures the **client's** choice of gateway and the per-turn
provider/model it sends (which the gateway honors via its selector). It does
NOT set the gateway's own secrets (API keys) or the ``METAFORGE_CHAT_HARNESS``
flag — those live on the gateway host, not here.

Precedence everywhere: explicit CLI flag > config file > environment > default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError

_MODES = ("ask", "auto", "plan")


def config_path() -> Path:
    """Location of the config file (``$FORGE_CONFIG`` or ``~/.forge/config.json``)."""
    override = os.environ.get("FORGE_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".forge" / "config.json"


@dataclass
class ForgeConfig:
    """Persisted client preferences."""

    gateway_url: str | None = None
    provider: str | None = None
    model: str | None = None
    mode: str = "ask"

    @classmethod
    def load(cls, path: Path | None = None) -> ForgeConfig:
        p = path or config_path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f: data.get(f) for f in ("gateway_url", "provider", "model", "mode")}
        cfg = cls(**{k: v for k, v in known.items() if v is not None})
        if cfg.mode not in _MODES:
            cfg.mode = "ask"
        return cfg

    def save(self, path: Path | None = None) -> Path:
        p = path or config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return p


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: str | None, input_fn: Callable[[str], str]) -> str | None:
    """Prompt with an optional default; empty answer keeps the default."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input_fn(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def run_wizard(
    client: ForgeClient,
    *,
    existing: ForgeConfig,
    input_fn: Callable[[str], str] = input,
    make_client: Callable[[str], ForgeClient] | None = None,
) -> ForgeConfig:
    """Interactively assemble a ForgeConfig, querying the gateway for choices.

    ``make_client`` builds a client for a chosen gateway URL (so provider/model
    lookups hit the URL the user just picked); defaults to reusing ``client``.
    """
    print("forge configuration wizard — Ctrl-C to keep current values\n")

    # 1) Gateway URL
    gateway_url = _ask("Gateway URL", existing.gateway_url or "http://localhost:8000", input_fn)
    probe = make_client(gateway_url) if (make_client and gateway_url) else client

    # 2) Provider — list what the gateway knows, mark configured ones
    providers: list[dict[str, Any]] = []
    active_provider = None
    try:
        payload = probe.list_harness_providers()
        providers = payload.get("providers", [])
        active_provider = payload.get("active_provider")
    except ForgeClientError as exc:
        print(f"  (couldn't reach {gateway_url} to list providers: {exc})")

    provider = existing.provider or active_provider
    if providers:
        print("\nAvailable providers (✓ = configured on the gateway):")
        for i, p in enumerate(providers, 1):
            mark = "✓" if p.get("configured") else " "
            print(f"  {i:>2}. [{mark}] {p.get('id')}  ({p.get('family')})")
        pick = _ask("Choose a provider (number or id)", provider, input_fn)
        provider = _resolve_provider(pick, providers) or provider
    else:
        provider = _ask("Provider id", provider, input_fn)

    # 3) Model — live list if the gateway can, else free text
    model = existing.model
    if provider:
        models: list[str] = []
        try:
            mresp = probe.list_harness_models(provider)
            models = [m.get("id") for m in mresp.get("models", []) if m.get("id")]
        except ForgeClientError:
            models = []
        if models:
            print(f"\nModels for {provider} (first 20 shown):")
            for i, mid in enumerate(models[:20], 1):
                print(f"  {i:>2}. {mid}")
            pick = _ask("Choose a model (number or name)", model, input_fn)
            model = _resolve_model(pick, models) or pick
        else:
            model = _ask(f"Model for {provider} (free text)", model, input_fn)

    # 4) Default proposal-handling mode
    mode = _ask("Default mode (ask/auto/plan)", existing.mode, input_fn) or "ask"
    if mode not in _MODES:
        print(f"  (unknown mode {mode!r}; using 'ask')")
        mode = "ask"

    return ForgeConfig(gateway_url=gateway_url, provider=provider, model=model, mode=mode)


def _resolve_provider(pick: str | None, providers: list[dict[str, Any]]) -> str | None:
    if not pick:
        return None
    if pick.isdigit():
        idx = int(pick) - 1
        if 0 <= idx < len(providers):
            return str(providers[idx].get("id"))
        return None
    return pick


def _resolve_model(pick: str | None, models: list[str]) -> str | None:
    if not pick:
        return None
    if pick.isdigit():
        idx = int(pick) - 1
        if 0 <= idx < len(models):
            return models[idx]
        return None
    return pick


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------


def handle_config(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge config` — wizard (default), show, set, path."""
    sub = getattr(args, "config_command", None)
    path = config_path()

    if sub == "path":
        print(path)
        return None

    if sub == "show":
        cfg = ForgeConfig.load(path)
        print(json.dumps(asdict(cfg), indent=2))
        print(f"\n(from {path if path.exists() else '(defaults — no file yet)'})")
        return None

    if sub == "set":
        cfg = ForgeConfig.load(path)
        key = args.key
        if key not in ("gateway_url", "provider", "model", "mode"):
            print(f"Error: unknown key {key!r} (gateway_url|provider|model|mode)", file=sys.stderr)
            return None
        if key == "mode" and args.value not in _MODES:
            print(f"Error: mode must be one of {_MODES}", file=sys.stderr)
            return None
        setattr(cfg, key, args.value)
        saved = cfg.save(path)
        print(f"{key} = {args.value}  → {saved}")
        return None

    # Default: interactive wizard
    existing = ForgeConfig.load(path)
    cfg = run_wizard(
        client,
        existing=existing,
        make_client=lambda url: ForgeClient(base_url=url),
    )
    saved = cfg.save(path)
    print("\nSaved configuration:")
    print(json.dumps(asdict(cfg), indent=2))
    print(f"\n→ {saved}")
    print("`forge chat` will now use these defaults (override per-run with flags).")
    return None
