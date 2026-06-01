"""Wiring tests for the Cloudflare tunnel + MCP sidecar (MET-482).

Locks in the dev-override contract that lets Claude cloud reach the MCP
HTTP sidecar over a public HTTPS hostname:

* a ``cloudflared`` named-tunnel service fronts ``mcp-http``;
* it shares the ``metaforge`` network so it can route to ``mcp-http:8765``;
* the tunnel token is sourced from the environment (never hardcoded);
* the now-public ``mcp-http`` endpoint can be guarded by the static
  API key (MET-338).

These are structural assertions on docker-compose.override.yml — they do
not require Docker. See docs/runbooks/cloudflare-mcp-tunnel.md.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERRIDE = REPO_ROOT / "docker-compose.override.yml"


def _services() -> dict:
    data = yaml.safe_load(OVERRIDE.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "services" in data, "override must declare services"
    return data["services"]


def _env_list(service: dict) -> list[str]:
    """compose ``environment:`` as a list of ``KEY=VALUE`` strings."""
    env = service.get("environment", [])
    assert isinstance(env, list), "environment is expected in list form here"
    return env


def test_cloudflared_service_present() -> None:
    services = _services()
    assert "cloudflared" in services, sorted(services)


def test_cloudflared_uses_official_image() -> None:
    cf = _services()["cloudflared"]
    assert str(cf.get("image", "")).startswith("cloudflare/cloudflared"), cf.get("image")


def test_cloudflared_runs_a_named_tunnel() -> None:
    """Named tunnel = ``tunnel run`` (token-driven), not an ephemeral
    ``tunnel --url`` quick tunnel."""
    command = str(_services()["cloudflared"].get("command", ""))
    assert "tunnel" in command and "run" in command, command
    assert "--url" not in command, "named tunnel must not use the ephemeral --url form"


def test_cloudflared_token_from_env() -> None:
    """The token must be injected via env, not baked into the file."""
    env = _env_list(_services()["cloudflared"])
    token = next((e for e in env if e.startswith("TUNNEL_TOKEN=")), None)
    assert token is not None, env
    assert "${CLOUDFLARE_TUNNEL_TOKEN" in token, (
        "TUNNEL_TOKEN must interpolate ${CLOUDFLARE_TUNNEL_TOKEN}, not a literal"
    )


def test_cloudflared_shares_metaforge_network_with_sidecar() -> None:
    """Both services must be on ``metaforge`` so the tunnel can route to
    ``mcp-http:8765`` by service name."""
    services = _services()
    assert "metaforge" in services["cloudflared"].get("networks", [])
    assert "metaforge" in services["mcp-http"].get("networks", [])


def test_cloudflared_depends_on_sidecar() -> None:
    depends = _services()["cloudflared"].get("depends_on", [])
    # short-form list or long-form mapping both acceptable
    names = depends if isinstance(depends, list) else list(depends)
    assert "mcp-http" in names, depends


def test_sidecar_exposes_mcp_port() -> None:
    ports = [str(p) for p in _services()["mcp-http"].get("ports", [])]
    assert any("8765" in p for p in ports), ports


def test_sidecar_supports_api_key_auth() -> None:
    """The public endpoint must be protectable via the MET-338 static key,
    sourced from the environment."""
    env = _env_list(_services()["mcp-http"])
    key = next((e for e in env if e.startswith("METAFORGE_MCP_API_KEY=")), None)
    assert key is not None, env
    assert "${METAFORGE_MCP_API_KEY" in key, (
        "METAFORGE_MCP_API_KEY must come from the environment"
    )
