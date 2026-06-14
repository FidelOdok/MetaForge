"""Installer for the capture hook (MET-499).

Registers the Claude Code capture adapter so it fires across sessions — at the
user level (``~/.claude/settings.json``, every repo) or per-project. Idempotent:
re-running replaces our hook entries (identified by the adapter-path marker)
without touching the user's other hooks/settings.

stdlib only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

_HOOK_EVENTS = ("PostToolUse", "Stop", "SessionEnd")
_POST_TOOL_MATCHER = "mcp__metaforge__.*"
_MARKER = "claude_code_adapter.py"  # identifies our hook entries on re-run/uninstall
_TOOL_FILES = ("__init__.py", "metaforge_capture.py", "parsers.py", "claude_code_adapter.py")


def _load_json(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _hook_command(adapter_path: Path) -> str:
    return f'python3 "{adapter_path}"'


def _hook_entry(event: str, command: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"hooks": [{"type": "command", "command": command}]}
    if event == "PostToolUse":
        entry["matcher"] = _POST_TOOL_MATCHER
    return entry


def _entry_is_ours(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks", []):
        if isinstance(h, dict) and _MARKER in str(h.get("command", "")):
            return True
    return False


def _strip_ours(settings: dict[str, Any]) -> None:
    """Remove our hook entries from every event, leaving everything else."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks.keys()):
        kept = [e for e in hooks.get(event, []) if not _entry_is_ours(e)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]


def install(
    source_dir: Path,
    settings_path: Path,
    *,
    metaforge_home: Path,
    mode: str = "copy",
    gateway_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Install the capture hook into ``settings_path``. Returns a summary.

    ``metaforge_home`` is the base for the copied tool + config (``~/.metaforge``
    in production; a tmp dir in tests).
    """
    # 1. resolve the adapter path (copy the tool to a stable home, or link).
    if mode == "copy":
        dest = metaforge_home / "capture-tool"
        dest.mkdir(parents=True, exist_ok=True)
        for fname in _TOOL_FILES:
            src = source_dir / fname
            if src.exists():
                shutil.copy2(src, dest / fname)
        adapter_path = dest / "claude_code_adapter.py"
    else:  # link — point at the source in place
        adapter_path = (source_dir / "claude_code_adapter.py").resolve()

    # 2. config so the hook reaches the gateway without shell-profile edits.
    if gateway_url or api_key:
        cfg_path = metaforge_home / "capture" / "config.json"
        cfg = _load_json(cfg_path)
        if gateway_url:
            cfg["gateway_url"] = gateway_url.rstrip("/")
        if api_key:
            cfg["api_key"] = api_key
        _write_json(cfg_path, cfg)

    # 3. idempotent deep-merge of our 3 hook entries into settings.
    settings = _load_json(settings_path)
    _strip_ours(settings)  # drop any prior install so re-run never duplicates
    hooks: dict[str, Any] = settings.setdefault("hooks", {})
    command = _hook_command(adapter_path)
    for event in _HOOK_EVENTS:
        hooks.setdefault(event, []).append(_hook_entry(event, command))
    _write_json(settings_path, settings)

    return {
        "settings_path": str(settings_path),
        "adapter_path": str(adapter_path),
        "mode": mode,
        "events": list(_HOOK_EVENTS),
        "gateway_url": gateway_url,
    }


def uninstall(settings_path: Path) -> dict[str, Any]:
    """Remove only our hook entries from ``settings_path``."""
    settings = _load_json(settings_path)
    _strip_ours(settings)
    _write_json(settings_path, settings)
    return {"settings_path": str(settings_path), "removed": True}
