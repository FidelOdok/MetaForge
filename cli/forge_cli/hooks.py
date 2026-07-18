"""Lifecycle hooks for `forge chat` (MET-561).

Run user-configured shell commands on chat lifecycle events — the CLI analog of
Claude Code hooks. Configured in ``.forge/hooks.json``::

    {
      "hooks": {
        "session_start": [{"command": "echo started >> ~/forge.log"}],
        "user_prompt":   [{"command": "notify-send \"$FORGE_HOOK_MESSAGE\""}],
        "post_turn":     [{"command": "./scripts/on_turn.sh"}],
        "session_end":   [{"command": "echo done"}]
      }
    }

Each command runs via the shell with the event payload provided both as env vars
(``FORGE_HOOK_EVENT``, ``FORGE_HOOK_MESSAGE``, ``FORGE_HOOK_THREAD_ID`` …) and as
JSON on stdin. Hooks are best-effort: a failure or timeout logs a warning and
never breaks the chat turn. No hooks run unless a config file exists.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# The lifecycle events forge chat emits.
HOOK_EVENTS = ("session_start", "user_prompt", "post_turn", "session_end")

_DEFAULT_HOOKS_PATH = ".forge/hooks.json"


def load_hooks(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load and normalize the hooks config; return ``{}`` if missing/invalid.

    Accepts either ``{"hooks": {...}}`` or a bare ``{event: [...]}`` mapping.
    Only known events with a list of command dicts are kept.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("hooks", data)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for event, specs in raw.items():
        if event in HOOK_EVENTS and isinstance(specs, list):
            out[event] = [s for s in specs if isinstance(s, dict) and s.get("command")]
    return out


class HookRunner:
    """Runs configured hooks for lifecycle events (best-effort)."""

    def __init__(
        self,
        hooks: dict[str, list[dict[str, Any]]],
        *,
        enabled: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.hooks = hooks
        self.enabled = enabled
        self.timeout = timeout

    @classmethod
    def from_path(
        cls,
        path: str | Path = _DEFAULT_HOOKS_PATH,
        *,
        enabled: bool = True,
        timeout: float = 30.0,
    ) -> HookRunner:
        return cls(load_hooks(path), enabled=enabled, timeout=timeout)

    def run(self, event: str, payload: dict[str, Any]) -> None:
        """Run every command registered for ``event`` (no-op if disabled/none)."""
        if not self.enabled:
            return
        for spec in self.hooks.get(event, []):
            command = spec.get("command")
            if command:
                self._run_one(str(command), event, payload)

    def _run_one(self, command: str, event: str, payload: dict[str, Any]) -> None:
        env = dict(os.environ)
        env["FORGE_HOOK_EVENT"] = event
        for key, value in payload.items():
            env[f"FORGE_HOOK_{key.upper()}"] = str(value)
        try:
            subprocess.run(  # noqa: S602 - hooks are user-configured shell commands by design
                command,
                shell=True,
                env=env,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"  hook '{event}' failed: {exc}", file=sys.stderr)
