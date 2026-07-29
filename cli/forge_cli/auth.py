"""``forge auth`` — provider login & selection, OpenClaw/OpenCode-style (MET-10).

Pick a provider, log in (API key via a hidden prompt, or ChatGPT/Codex via
browser OAuth), and choose the active provider/model — from the CLI, with the
credential pushed once to the gateway (the shared runtime) so chat + design
turns use it with no restart.

    forge auth list                 # providers + configured/active
    forge auth login                # interactive: provider → method → credential
    forge auth use <provider> [-m]  # set the durable active provider/model
    forge auth logout <provider>    # forget a stored credential

The gateway holds the credential (0600). OAuth runs the browser loopback on THIS
machine and pushes only the resulting token. Set METAFORGE_HARNESS_ADMIN_TOKEN
if the gateway requires it (the client sends it automatically).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError
from cli.forge_cli.config import _ask, _resolve_model, _resolve_provider

# ``forge codex-login`` is the OAuth-only path; ``forge auth login`` supersedes it
# with a provider/method picker (and can still drive the same OAuth flow).


def register_subparser(subparsers: Any) -> None:
    """Register the ``auth`` command group."""
    p = subparsers.add_parser("auth", help="Log in to providers and choose the active model")
    sub = p.add_subparsers(dest="auth_command", help="Auth subcommands")

    sub.add_parser("list", help="List providers with configured/active state")

    login = sub.add_parser("login", help="Log in to a provider (API key or OAuth)")
    login.add_argument("--provider", default=None, help="Provider id (skips the picker)")
    login.add_argument("--method", choices=["api-key", "oauth"], default=None, help="Login method")
    login.add_argument("--model", default=None, help="Also set this as the active model")
    login.add_argument(
        "--no-activate", action="store_true", help="Don't make this the active provider"
    )
    # OAuth flags (mirrors `codex-login`), forwarded to the browser flow.
    login.add_argument("--mode", choices=["auto", "loopback", "device", "manual"], default="auto")
    login.add_argument("--port", type=int, default=1455)
    login.add_argument("--no-browser", action="store_true")

    use = sub.add_parser("use", help="Set the active provider (and optionally model)")
    use.add_argument("provider", help="Provider id")
    use.add_argument("-m", "--model", default=None, help="Model id")

    logout = sub.add_parser("logout", help="Forget a provider's stored credential")
    logout.add_argument("provider", help="Provider id")


# --- OAuth: run the browser loopback locally, return the token blob ----------


def _default_oauth_login(args: argparse.Namespace) -> dict[str, Any]:
    """Run the Codex OAuth flow on THIS machine and return the auth.json token blob.

    Reuses ``codex_login.login`` (browser loopback / device / manual) into a
    throwaway dir so we can push the tokens to the gateway without touching the
    user's real ``~/.codex``.
    """
    from orchestrator.harness.providers import codex_login

    tmp = Path(tempfile.mkdtemp(prefix="forge-auth-"))
    try:
        path = asyncio.run(
            codex_login.login(
                mode=args.mode,
                port=args.port,
                open_browser=not args.no_browser,
                codex_home=tmp,
            )
        )
        return json.loads(Path(path).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- subcommands -------------------------------------------------------------


def _cmd_list(client: ForgeClient) -> list[dict[str, Any]]:
    payload = client.list_harness_providers()
    active = (payload.get("active_provider") or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for p in payload.get("providers", []):
        pid = str(p.get("id"))
        rows.append(
            {
                "provider": pid,
                "family": p.get("family"),
                "configured": "✓" if p.get("configured") else "",
                "active": "✓" if pid.lower() == active else "",
            }
        )
    return rows


def _pick_provider(client: ForgeClient, given: str | None, input_fn: Callable[[str], str]) -> str:
    """Return a provider id — from ``given`` or an interactive numbered menu."""
    payload = client.list_harness_providers()
    providers = payload.get("providers", [])
    if given:
        return given.strip().lower()
    if not providers:
        picked = _ask("Provider id", None, input_fn)
        if not picked:
            raise ForgeClientError("no provider selected")
        return picked.strip().lower()
    print("\nProviders (✓ = configured on the gateway):")
    for i, p in enumerate(providers, 1):
        mark = "✓" if p.get("configured") else " "
        print(f"  {i:>2}. [{mark}] {p.get('id')}  ({p.get('family')})")
    pick = _ask("Choose a provider (number or id)", None, input_fn)
    resolved = _resolve_provider(pick, providers)
    if not resolved:
        raise ForgeClientError(f"unknown provider: {pick!r}")
    return resolved.strip().lower()


def _provider_family(client: ForgeClient, provider: str) -> str | None:
    for p in client.list_harness_providers().get("providers", []):
        if str(p.get("id")).lower() == provider.lower():
            return p.get("family")
    return None


def _cmd_login(
    client: ForgeClient,
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    oauth_login: Callable[[argparse.Namespace], dict[str, Any]] = _default_oauth_login,
) -> None:
    provider = _pick_provider(client, args.provider, input_fn)
    family = _provider_family(client, provider)

    # Method: explicit flag → else default by family (codex → oauth, else api-key).
    method = args.method
    if method is None:
        method = "oauth" if family == "codex" else "api-key"

    if method == "oauth":
        print(f"\nStarting OAuth login for {provider} …")
        tokens = oauth_login(args)
        client.set_credential(provider, method="oauth", tokens=tokens)
        print(f"✓ Logged in to {provider} (OAuth) — credential stored on the gateway.")
    else:
        key = getpass_fn(f"API key for {provider}: ").strip()
        if not key:
            raise ForgeClientError("no API key entered")
        base_url = _ask("Base URL (optional, blank for default)", None, input_fn) or None
        client.set_credential(provider, method="api_key", api_key=key, base_url=base_url)
        print(f"✓ Stored an API key for {provider} on the gateway.")

    if args.no_activate:
        return
    model = args.model or _choose_model(client, provider, input_fn)
    client.set_selection(provider, model)
    _mirror_local_config(provider, model)
    print(f"✓ Active provider → {provider}" + (f" · {model}" if model else ""))


def _choose_model(client: ForgeClient, provider: str, input_fn: Callable[[str], str]) -> str | None:
    """Offer a model list (live) or free-text; empty = leave unset."""
    try:
        models = [m.get("id") for m in client.list_harness_models(provider).get("models", [])]
        models = [m for m in models if m]
    except ForgeClientError:
        models = []
    if models:
        print(f"\nModels for {provider} (first 20):")
        for i, mid in enumerate(models[:20], 1):
            print(f"  {i:>2}. {mid}")
        pick = _ask("Choose a model (number or name, blank to skip)", None, input_fn)
        return _resolve_model(pick, models) or (pick or None)
    return _ask(f"Model for {provider} (optional)", None, input_fn) or None


def _cmd_use(client: ForgeClient, args: argparse.Namespace) -> None:
    provider = args.provider.strip().lower()
    client.set_selection(provider, args.model)
    _mirror_local_config(provider, args.model)
    print(f"✓ Active provider → {provider}" + (f" · {args.model}" if args.model else ""))


def _cmd_logout(client: ForgeClient, args: argparse.Namespace) -> None:
    provider = args.provider.strip().lower()
    client.delete_credential(provider)
    print(f"✓ Forgot the stored credential for {provider}.")


def _mirror_local_config(provider: str, model: str | None) -> None:
    """Best-effort: mirror the selection into ~/.forge/config.json so the TUI shows it."""
    try:
        from cli.forge_cli.config import ForgeConfig

        cfg = ForgeConfig.load()
        cfg.provider = provider
        if model:
            cfg.model = model
        cfg.save()
    except Exception:  # noqa: BLE001 - purely cosmetic mirror; never fail the command
        pass


def handle_auth(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch ``forge auth`` subcommands. Handlers print themselves and return
    None, except ``list`` which returns rows for the table formatter."""
    cmd = getattr(args, "auth_command", None)
    if cmd == "list":
        return _cmd_list(client)
    if cmd == "login":
        _cmd_login(client, args)
        return None
    if cmd == "use":
        _cmd_use(client, args)
        return None
    if cmd == "logout":
        _cmd_logout(client, args)
        return None
    print("usage: forge auth {list|login|use|logout}")
    return None
