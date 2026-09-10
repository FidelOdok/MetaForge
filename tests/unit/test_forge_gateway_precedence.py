"""Gateway-URL precedence in the CLI (MET-729).

An environment variable exists to override persisted config for one
invocation. It used to sit *below* the saved config, because ``main()`` passed
``config.gateway_url`` into ``ForgeClient`` unconditionally and ``ForgeClient``
consults ``METAFORGE_GATEWAY_URL`` only when its ``base_url`` is falsy.

That inversion was not theoretical. A unit test that set
``METAFORGE_GATEWAY_URL`` to a dead port to keep itself local was silently
ignored, and instead POSTed documents into the shared dev deployment on every
run -- 11 ``knowledge_document_ingested`` events from pytest temp paths in one
week, plus 18 ``lightrag_ingest_not_persisted`` errors, found by mining that
deployment's own logs.

The chain, highest first: ``--gateway-url`` → ``METAFORGE_GATEWAY_URL`` →
saved config → ``ForgeClient``'s built-in default.

These drive the real ``main()`` and capture the URL it actually hands to
``ForgeClient``. A first draft asserted against a *copy* of the resolution
expression instead, and reverting the fix left it green -- a regression test
that cannot catch the regression. Worth stating plainly, because a mirrored
expression looks like a test of the code and is a test of the mirror.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cli.forge_cli.main as main_module
from cli.forge_cli.client import ForgeClient
from cli.forge_cli.config import ForgeConfig

SAVED = "http://saved-config:8000"
FROM_ENV = "http://from-env:9000"
FROM_FLAG = "http://from-flag:7000"


@pytest.fixture
def saved_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config file on disk with a gateway_url, as ``forge config`` writes."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"gateway_url": SAVED, "mode": "ask"}), encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(path))
    return path


@pytest.fixture
def resolved_url(monkeypatch: pytest.MonkeyPatch):
    """Run the real ``main()`` and return the base_url it gave ForgeClient.

    ``ForgeClient`` is replaced with a recorder and the dispatched handler
    with a no-op, so nothing opens a socket -- the point is only which URL
    ``main()`` resolved.
    """
    captured: dict[str, str | None] = {}

    class _Recorder:
        def __init__(self, base_url: str | None = None, **_kw: object) -> None:
            captured["base_url"] = base_url

    monkeypatch.setattr(main_module, "ForgeClient", _Recorder)
    monkeypatch.setitem(main_module._HANDLERS, "sources", lambda _a, _c: None)

    def _run(gateway_flag: str | None = None) -> str | None:
        # --gateway-url is a GLOBAL option, so it has to precede the
        # subcommand; argparse rejects it afterwards.
        captured.clear()
        argv = (["--gateway-url", gateway_flag] if gateway_flag else []) + ["sources", "list"]
        main_module.main(argv)
        return captured.get("base_url")

    return _run


class TestPrecedence:
    def test_the_env_var_beats_a_saved_config(self, saved_config, monkeypatch, resolved_url):
        """THE regression. Before the fix this resolved to the saved config."""
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", FROM_ENV)
        assert ForgeConfig.load().gateway_url == SAVED, "fixture did not take effect"

        assert resolved_url() == FROM_ENV

    def test_the_flag_beats_the_env_var(self, saved_config, monkeypatch, resolved_url):
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", FROM_ENV)

        assert resolved_url(FROM_FLAG) == FROM_FLAG

    def test_the_saved_config_is_used_when_no_env_var_is_set(
        self, saved_config, monkeypatch, resolved_url
    ):
        """Correcting the precedence must not make the saved config useless --
        it is still what ``forge config set gateway_url`` is for."""
        monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)

        assert resolved_url() == SAVED

    def test_nothing_configured_leaves_the_client_to_its_own_default(
        self, monkeypatch, tmp_path, resolved_url
    ):
        monkeypatch.setenv("FORGE_CONFIG", str(tmp_path / "missing.json"))
        monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)

        # Falsy, which is precisely what lets ForgeClient apply its default.
        assert not resolved_url()


class TestClientFallback:
    """The mechanism behind the bug, pinned so the fix cannot be undone by
    changing ``ForgeClient`` instead of ``main()``."""

    def test_the_client_reads_the_env_var_when_given_no_base_url(self, monkeypatch):
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", FROM_ENV)

        assert ForgeClient().base_url == FROM_ENV

    def test_an_explicit_base_url_suppresses_the_env_var(self, monkeypatch):
        """Passing the saved config in as base_url is exactly what silenced
        ``METAFORGE_GATEWAY_URL``, so ``main()`` must not pass a value it has
        not already given the env var a chance to beat."""
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", FROM_ENV)

        assert ForgeClient(base_url=SAVED).base_url == SAVED
