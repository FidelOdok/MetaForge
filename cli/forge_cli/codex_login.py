"""``forge codex-login`` — log in to ChatGPT/Codex via OAuth (MET-550).

Performs MetaForge's own OAuth login so no external ``codex`` CLI is needed.
Writes ``CODEX_HOME/auth.json`` for the ``openai-codex`` provider.

Headless boxes (e.g. fidel-dev): forward the callback port first, then run
with ``--no-browser`` and open the printed URL in your local browser::

    ssh -L 1455:localhost:1455 claude@fidel-dev
    python -m cli.forge_cli codex-login --no-browser

If you cannot forward the port, use ``--mode manual`` and paste the redirect URL.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any


def register_subparser(subparsers: Any) -> None:
    """Register the ``codex-login`` subcommand."""
    p = subparsers.add_parser(
        "codex-login",
        help="Log in to ChatGPT/Codex via OAuth (writes CODEX_HOME/auth.json)",
    )
    p.add_argument(
        "--mode",
        choices=["auto", "loopback", "device", "manual"],
        default="auto",
        help="OAuth flow (default: auto = device-code, falling back to loopback)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=1455,
        help="Local callback bind port (default: 1455; forward it with ssh -L on a remote box)",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't try to open a browser — just print the authorization URL",
    )
    p.add_argument(
        "--codex-home",
        default=None,
        help="Directory to write auth.json into (default: $CODEX_HOME or ~/.codex)",
    )


def handle_codex_login(args: argparse.Namespace, client: Any) -> None:
    """Handle ``forge codex-login``. Prints the written path; returns None."""
    from orchestrator.harness.providers import codex_login

    path = asyncio.run(
        codex_login.login(
            mode=args.mode,
            port=args.port,
            open_browser=not args.no_browser,
            codex_home=Path(args.codex_home) if args.codex_home else None,
        )
    )
    print(f"Wrote Codex credentials to {path}")
