"""Harness capability endpoints — providers / models / tools (MET-548)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.harness import router


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_providers_reports_active_and_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    body = client.get("/v1/harness/providers").json()
    assert body["active_provider"] == "openrouter"
    assert body["active_model"] == "openai/gpt-4o-mini"
    ids = {p["id"] for p in body["providers"]}
    assert "openrouter" in ids and "anthropic" in ids
    assert len(body["providers"]) >= 30

    by_id = {p["id"]: p for p in body["providers"]}
    assert by_id["openrouter"]["configured"] is True  # key set
    assert by_id["ollama"]["configured"] is True  # keyless/local
    assert by_id["anthropic"]["configured"] is False  # key unset
    # configured providers sort first
    assert body["providers"][0]["configured"] is True


def test_active_provider_configured_via_metaforge_llm_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The active provider's key may live in METAFORGE_LLM_API_KEY (deployments
    # whose provider key-env name differs from the registry default).
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_API_KEY", "sk-active")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    by_id = {p["id"]: p for p in client.get("/v1/harness/providers").json()["providers"]}
    assert by_id["openrouter"]["configured"] is True  # via METAFORGE_LLM_API_KEY


def test_models_unknown_provider_returns_empty(client: TestClient) -> None:
    body = client.get("/v1/harness/models", params={"provider": "nope-xyz"}).json()
    assert body == {"provider": "nope-xyz", "models": [], "source": "none"}


def test_models_non_openai_family_returns_empty(client: TestClient) -> None:
    # anthropic family isn't live-fetched → empty, source none (UI free-texts)
    body = client.get("/v1/harness/models", params={"provider": "anthropic"}).json()
    assert body["provider"] == "anthropic"
    assert body["source"] == "none"


def test_list_tools_empty_with_default_bridge(client: TestClient) -> None:
    # Default gateway bridge is an empty InMemoryMcpBridge.
    assert client.get("/v1/harness/tools").json() == []
