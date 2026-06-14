"""Capture hook installer (MET-499).

install/uninstall merge our hooks into a settings.json idempotently while
preserving the user's other config; copy mode stages the tool; config.json
is written and the core reads it as a gateway-url fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.session_capture import installer
from tools.session_capture import metaforge_capture as mc

_SOURCE = Path(installer.__file__).resolve().parent


def _read(p: Path) -> dict:
    return json.loads(p.read_text())


class TestInstall:
    def test_writes_three_hooks(self, tmp_path: Path) -> None:
        sp = tmp_path / ".claude" / "settings.json"
        installer.install(_SOURCE, sp, metaforge_home=tmp_path / "mf", mode="link")
        hooks = _read(sp)["hooks"]
        assert set(hooks) == {"PostToolUse", "Stop", "SessionEnd"}
        assert hooks["PostToolUse"][0]["matcher"] == "mcp__metaforge__.*"
        assert "claude_code_adapter.py" in hooks["Stop"][0]["hooks"][0]["command"]

    def test_preserves_existing_settings_and_hooks(self, tmp_path: Path) -> None:
        sp = tmp_path / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text(
            json.dumps(
                {
                    "model": "opus",
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo hi"}],
                            }
                        ]
                    },
                }
            )
        )
        installer.install(_SOURCE, sp, metaforge_home=tmp_path / "mf", mode="link")
        data = _read(sp)
        assert data["model"] == "opus"  # untouched
        ptu = data["hooks"]["PostToolUse"]
        cmds = [h["hooks"][0]["command"] for h in ptu]
        assert "echo hi" in cmds  # user's hook preserved
        assert any("claude_code_adapter.py" in c for c in cmds)  # ours added

    def test_idempotent(self, tmp_path: Path) -> None:
        sp = tmp_path / ".claude" / "settings.json"
        installer.install(_SOURCE, sp, metaforge_home=tmp_path / "mf", mode="link")
        installer.install(_SOURCE, sp, metaforge_home=tmp_path / "mf", mode="link")
        hooks = _read(sp)["hooks"]
        for event in ("PostToolUse", "Stop", "SessionEnd"):
            ours = [e for e in hooks[event] if "claude_code_adapter.py" in e["hooks"][0]["command"]]
            assert len(ours) == 1  # no duplicate on re-run

    def test_copy_mode_stages_tool_and_points_hook_there(self, tmp_path: Path) -> None:
        sp = tmp_path / ".claude" / "settings.json"
        home = tmp_path / "mf"
        summary = installer.install(_SOURCE, sp, metaforge_home=home, mode="copy")
        copied = home / "capture-tool" / "claude_code_adapter.py"
        assert copied.exists()
        assert (home / "capture-tool" / "metaforge_capture.py").exists()
        assert str(copied) in summary["adapter_path"]
        assert str(copied) in _read(sp)["hooks"]["Stop"][0]["hooks"][0]["command"]

    def test_writes_config(self, tmp_path: Path) -> None:
        sp = tmp_path / ".claude" / "settings.json"
        home = tmp_path / "mf"
        installer.install(
            _SOURCE,
            sp,
            metaforge_home=home,
            mode="link",
            gateway_url="http://fidel-dev:8000/",
            api_key="k1",
        )
        cfg = _read(home / "capture" / "config.json")
        assert cfg["gateway_url"] == "http://fidel-dev:8000"  # trailing slash stripped
        assert cfg["api_key"] == "k1"


class TestUninstall:
    def test_removes_only_ours(self, tmp_path: Path) -> None:
        sp = tmp_path / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo hi"}],
                            }
                        ]
                    }
                }
            )
        )
        installer.install(_SOURCE, sp, metaforge_home=tmp_path / "mf", mode="link")
        installer.uninstall(sp)
        data = _read(sp)
        # our hooks gone; the user's Bash hook survives
        ptu = data.get("hooks", {}).get("PostToolUse", [])
        cmds = [h["hooks"][0]["command"] for h in ptu]
        assert cmds == ["echo hi"]
        assert "Stop" not in data.get("hooks", {})


class TestCoreConfigFallback:
    def test_gateway_url_from_config_when_env_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)
        cfg = tmp_path / ".metaforge" / "capture" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"gateway_url": "http://fidel-dev:8000"}))
        assert mc._gateway_url() == "http://fidel-dev:8000"

    def test_env_overrides_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://env:9000")
        cfg = tmp_path / ".metaforge" / "capture" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"gateway_url": "http://cfg:8000"}))
        assert mc._gateway_url() == "http://env:9000"
