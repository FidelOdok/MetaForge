"""Unit tests for `forge chat` lifecycle hooks (MET-561)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli.forge_cli.hooks import HookRunner, load_hooks


def _write_hooks(tmp_path: Path, config: dict[str, Any]) -> Path:
    p = tmp_path / "hooks.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    return p


def test_load_hooks_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_hooks(tmp_path / "nope.json") == {}


def test_load_hooks_invalid_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "hooks.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_hooks(p) == {}


def test_load_hooks_normalizes_known_events(tmp_path: Path) -> None:
    p = _write_hooks(
        tmp_path,
        {
            "hooks": {
                "user_prompt": [{"command": "echo hi"}],
                "bogus_event": [{"command": "nope"}],  # unknown event dropped
                "post_turn": [{"no_command": 1}],  # missing command dropped
            }
        },
    )
    hooks = load_hooks(p)
    assert "user_prompt" in hooks
    assert "bogus_event" not in hooks
    assert hooks["post_turn"] == []


def test_load_hooks_accepts_bare_mapping(tmp_path: Path) -> None:
    p = _write_hooks(tmp_path, {"session_start": [{"command": "echo x"}]})
    hooks = load_hooks(p)
    assert hooks["session_start"] == [{"command": "echo x"}]


def test_hook_runner_runs_command_and_passes_env(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    # command writes the event + message env vars to the marker file
    cmd = f'printf "%s:%s" "$FORGE_HOOK_EVENT" "$FORGE_HOOK_MESSAGE" > {marker}'
    runner = HookRunner({"user_prompt": [{"command": cmd}]})
    runner.run("user_prompt", {"message": "hello", "thread_id": "t-1"})
    assert marker.read_text() == "user_prompt:hello"


def test_hook_runner_disabled_is_noop(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    runner = HookRunner({"session_start": [{"command": f"touch {marker}"}]}, enabled=False)
    runner.run("session_start", {})
    assert not marker.exists()


def test_hook_runner_no_hooks_for_event_is_noop() -> None:
    runner = HookRunner({"user_prompt": [{"command": "echo x"}]})
    runner.run("post_turn", {})  # no hooks for post_turn — must not raise


def test_hook_runner_failing_command_does_not_raise() -> None:
    runner = HookRunner({"user_prompt": [{"command": "exit 7"}]})
    runner.run("user_prompt", {})  # non-zero exit is swallowed (best-effort)


def test_hook_runner_from_path_missing_is_empty_and_safe(tmp_path: Path) -> None:
    runner = HookRunner.from_path(tmp_path / "absent.json")
    assert runner.hooks == {}
    runner.run("session_start", {})  # no-op, no raise
