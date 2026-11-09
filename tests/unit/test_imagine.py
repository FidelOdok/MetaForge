"""The Imagine stage: the formgraph studio, its twin bridge and the handover gate (FORGE-821)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from api_gateway.imagine import engine_page
from api_gateway.imagine import routes as imagine_routes
from api_gateway.imagine.service import ImagineService, sanitize
from api_gateway.operations import publisher as ops
from mcp_core.annotations import DESTRUCTIVE
from mcp_core.context import McpCallContext, with_context
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    HumanAuthorityRequiredError,
    never_auto_approved,
    requires_human_authority,
)
from tests.unit.imagine_fixtures import base_config, full_config, mesh_glb
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import record_staleness
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c11"
BASE = f"/v1/twin/projects/{PROJECT}/imagine"
ENGINE_DIR = Path(engine_page.__file__).parent / "engine"


@pytest.fixture(autouse=True)
def publisher() -> Iterator[Any]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def service(twin: InMemoryTwinAPI) -> Iterator[ImagineService]:
    svc = ImagineService(twin)
    imagine_routes.set_service(svc)
    yield svc
    imagine_routes.set_service(None)


@pytest.fixture
async def client(service: ImagineService) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(imagine_routes.router)
    imagine_routes.get_service = lambda: service  # type: ignore[assignment]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture(autouse=True)
def _restore_get_service() -> Iterator[None]:
    original = imagine_routes.get_service
    yield
    imagine_routes.get_service = original  # type: ignore[assignment]


async def _put(client: httpx.AsyncClient, config: dict[str, Any] | None = None) -> dict[str, Any]:
    res = await client.put(f"{BASE}/studio", json={"config": config or full_config()})
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


async def _event(
    client: httpx.AsyncClient, kind: str, data: dict[str, Any], event_id: str | None = None
) -> httpx.Response:
    return await client.post(
        f"{BASE}/events", json={"event_id": event_id or uuid4().hex, "kind": kind, "data": data}
    )


async def _add_mesh(twin: InMemoryTwinAPI) -> None:
    now = datetime.now(UTC)
    await twin.create_work_product(
        WorkProduct(
            id=uuid4(),
            name="Concept mesh",
            type=WorkProductType.RENDER,
            domain="mechanical",
            file_path="",
            content_hash=uuid4().hex,
            format="glb",
            metadata=mesh_glb(),
            created_at=now,
            updated_at=now,
            created_by="test",
            project_id=UUID(PROJECT),
        )
    )


async def _make_ready(client: httpx.AsyncClient, twin: InMemoryTwinAPI) -> None:
    cfg = full_config()
    for i, row in enumerate(cfg["programme"]["ledger"]):
        res = await _event(client, "ledger", {"id": f"l{i}", "index": i, "status": row["status"]})
        assert res.status_code == 200, res.text
    for a in cfg["programme"]["assumptions"]:
        res = await _event(client, "assumption_confirmed", {"id": a["id"], "confirmed": True})
        assert res.status_code == 200, res.text
    res = await _event(
        client,
        "screening",
        {
            "id": "latest",
            "engine": "formgraph@2.0.0",
            "rule_version": "formgraph@2.0.0#im",
            "process": "im",
            "rules": [{"id": "wall", "label": "Wall thickness", "status": "pass"}],
        },
    )
    assert res.status_code == 200, res.text
    await _add_mesh(twin)


# ── the engine page ──────────────────────────────────────────────────────


async def test_the_engine_page_is_served_under_a_strict_csp(client: httpx.AsyncClient) -> None:
    await _put(client)
    res = await client.get(f"{BASE}/engine")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    csp = res.headers["content-security-policy"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["style-src"] == "'self'"
    assert "unsafe-eval" not in csp
    assert "'unsafe-inline'" not in directives["script-src"]
    assert directives["frame-ancestors"] == "'self'"
    assert directives["connect-src"] == "'none'"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "no-store"


async def test_the_engine_references_no_external_origin(client: httpx.AsyncClient) -> None:
    await _put(client)
    page = (await client.get(f"{BASE}/engine")).text
    refs = re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*"([^"]*)"', page)
    assert refs, "the page loads its scripts and stylesheet"
    for ref in refs:
        assert not re.match(r"^(https?:)?//", ref), ref
        assert ref.startswith("engine/assets/"), ref
    # No inline script that runs (CSP forbids it): only the JSON data blocks.
    for attrs, _body in re.findall(r"<script\b([^>]*)>(.*?)</script>", page, re.S):
        assert "src=" in attrs or 'type="application/json"' in attrs, attrs
    assert not re.search(r"\son[a-z]+\s*=", page.split('id="fg-config"')[0]), "no inline handlers"
    for name in ("engine.js", "engine.css"):
        text = (ENGINE_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"(src|href)\s*=\s*['\"]https?://", text), name
        assert not re.search(r"@import|url\(\s*['\"]?https?:", text), name
        assert "fonts.googleapis" not in text and "cdnjs" not in text and "jsdelivr" not in text
        assert "onclick=" not in text


async def test_every_asset_the_page_names_is_served(client: httpx.AsyncClient) -> None:
    await _put(client)
    page = (await client.get(f"{BASE}/engine")).text
    names = re.findall(r"engine/assets/([A-Za-z0-9_.-]+)\?v=", page)
    assert set(names) == set(engine_page.ASSETS)
    for name in names:
        res = await client.get(f"{BASE}/engine/assets/{name}")
        assert res.status_code == 200, name
        assert res.content == engine_page.asset_bytes(name)
    assert (await client.get(f"{BASE}/engine/assets/..%2Fservice.py")).status_code == 404
    assert (await client.get(f"{BASE}/engine/assets/engine.html")).status_code == 404


def test_the_vendored_three_js_matches_its_manifest() -> None:
    manifest = json.loads((ENGINE_DIR / "vendor" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.128.0"
    for name, entry in manifest["files"].items():
        digest = hashlib.sha256((ENGINE_DIR / "vendor" / name).read_bytes()).hexdigest()
        assert digest == entry["sha256"], name


def test_the_config_embedding_escapes_a_closing_tag() -> None:
    cfg = base_config()
    cfg["product"]["name"] = "</script><script>alert(1)</script>"[:60]
    page = engine_page.embed_config(cfg)
    slot = re.search(r'<script id="fg-config" type="application/json">(.*?)</script>', page, re.S)
    assert slot is not None
    raw = slot.group(1)
    assert "</" not in raw and "<\\/script>" in raw
    assert json.loads(raw)["product"]["name"] == cfg["product"]["name"]
    assert "<title>&lt;/script&gt;" in page


def test_a_hosted_config_never_reads_a_context_folder_or_a_file() -> None:
    cfg = base_config()
    cfg["context"] = "context"
    with pytest.raises(engine_page.ConfigError):
        engine_page.validate_config(cfg)
    cfg = full_config()
    cfg["context"]["references"] = {
        "schemaVersion": 1,
        "references": [{"id": "look", "kind": "image", "file": "../../etc/passwd"}],
    }
    with pytest.raises(engine_page.ConfigError) as err:
        engine_page.validate_config(cfg)
    assert any("cannot read files" in e for e in err.value.errors)
    cfg["context"]["references"]["references"] = [
        {"id": "look", "kind": "image", "data": "https://evil.example/x.png"}
    ]
    with pytest.raises(engine_page.ConfigError):
        engine_page.validate_config(cfg)


def test_the_engine_bridges_to_its_host_and_freezes_read_only() -> None:
    js = (ENGINE_DIR / "engine.js").read_text(encoding="utf-8")
    for message in ("fg:ready", "fg:write", "fg:height", "fg:select", "fg:download"):
        assert f"'{message}'" in js, message
    assert "m.type==='fg:snapshot'" in js and "m.type==='fg:readonly'" in js
    assert "e.source!==window.parent" in js, "only the parent frame is listened to"
    assert "if(!HOSTED&&CTX&&window.claude&&claude.use)" in js, "standalone db path kept"
    assert "function applyReadonly()" in js and "el.disabled=true" in js
    assert "if(READONLY){toast(" in js, "a write after the handover is refused in the page"


# ── the studio ───────────────────────────────────────────────────────────


async def test_no_studio_reads_as_none(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{BASE}/studio")).json()
    assert body["status"] == "none" and body["revision"] == 0
    assert body["engine_url"] == f"/v1/twin/projects/{PROJECT}/imagine/engine"
    assert (await client.get(f"{BASE}/engine")).status_code == 404


async def test_put_writes_a_new_revision(client: httpx.AsyncClient) -> None:
    first = await _put(client)
    assert first["status"] == "draft" and first["config_ref"] == "IMG-STUDIO@1"
    second = await _put(client)
    assert second["revision"] == 2 and second["config_ref"] == "IMG-STUDIO@2"


async def test_an_invalid_config_is_refused_with_its_errors(client: httpx.AsyncClient) -> None:
    cfg = base_config()
    cfg["process"]["material"] = "unobtainium"
    res = await client.put(f"{BASE}/studio", json={"config": cfg})
    assert res.status_code == 422
    assert any("unobtainium" in e for e in res.json()["detail"]["errors"])


# ── events ───────────────────────────────────────────────────────────────


async def test_a_choice_becomes_a_design_decision_pinned_to_the_studio(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    res = await _event(client, "choice", {"id": "th", "pick": "th-b"})
    assert res.status_code == 200, res.text
    (ref,) = res.json()["mapped_to"]
    assert ref.startswith("decision:")
    wp = await twin.get_work_product(UUID(ref.split(":", 1)[1]))
    assert wp is not None and wp.type == WorkProductType.DESIGN_DECISION
    assert "Temperature sensor, budget" in wp.name
    studio = (await client.get(f"{BASE}/studio")).json()
    assert studio["collections"]["choices"]["th"]["pick"] == "th-b"
    bad = await _event(client, "choice", {"id": "th", "pick": "not-a-candidate"})
    assert bad.status_code == 422


async def test_each_kind_maps_into_the_twin(client: httpx.AsyncClient) -> None:
    await _put(client)
    req = await _event(
        client,
        "requirement",
        {"id": "mount", "st": "met", "ev": "photo", "verify": "demonstration"},
    )
    assert req.json()["mapped_to"] == ["CS-IMAGINE@1"]
    scen = await _event(client, "scenario", {"id": "rain", "text": "Left out in the rain"})
    assert scen.json()["mapped_to"] == ["CS-IMAGINE@2"]
    led = await _event(client, "ledger", {"id": "l0", "index": 0, "status": "mapped"})
    assert led.json()["mapped_to"] == ["INT-IMG-L0@1"]
    asm = await _event(client, "assumption_confirmed", {"id": "cover", "confirmed": True})
    assert asm.json()["mapped_to"][0].startswith("assumption:")
    edit = await _event(
        client,
        "context_edit",
        {"id": "e1", "kind": "machine", "machine": {"name": "Site moulder", "process": "im"}},
    )
    assert edit.json()["mapped_to"] == ["MFC-PROJECT@1"]
    scr = await _event(
        client, "screening", {"id": "latest", "rule_version": "formgraph@2.0.0#im", "rules": []}
    )
    assert scr.status_code == 200 and scr.json()["mapped_to"][0].startswith("evidence:")


async def test_nothing_from_the_browser_is_stored_above_v2(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    res = await _event(
        client,
        "screening",
        {
            "id": "latest",
            "verified": True,
            "verification_level": "V4",
            "rules": [{"id": "wall", "status": "pass", "authority": "verified"}],
        },
    )
    (ref,) = res.json()["mapped_to"]
    node = await twin.graph.get_node(UUID(ref.split(":", 1)[1]))
    meta = node.metadata  # type: ignore[union-attr]
    assert meta["verification_level"] == "V2"
    assert meta["dfm_status"] == "preliminary_guidance"
    assert "verified" not in json.dumps(meta["rules"])
    stored = (await client.get(f"{BASE}/studio")).json()["collections"]["screening"]["latest"]
    assert "verified" not in stored and "verification_level" not in stored
    assert sanitize({"a": {"approved": True, "b": 1}}) == {"a": {"b": 1}}


async def test_an_event_is_idempotent_per_event_id(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    first = await _event(client, "choice", {"id": "th", "pick": "th-b"}, event_id="ev-1")
    again = await _event(client, "choice", {"id": "th", "pick": "th-b"}, event_id="ev-1")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["mapped_to"] == first.json()["mapped_to"]
    decisions = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_DECISION, project_id=UUID(PROJECT)
    )
    assert len(decisions) == 1


async def test_an_agent_cannot_confirm_an_assumption(client: httpx.AsyncClient) -> None:
    await _put(client)
    res = await client.post(
        f"{BASE}/events",
        json={
            "event_id": "a1",
            "kind": "assumption_confirmed",
            "data": {"id": "cover", "confirmed": True},
        },
        headers={"X-MetaForge-Surface": "agent"},
    )
    assert res.status_code == 403


# ── handover and rework ──────────────────────────────────────────────────


async def test_handover_is_refused_with_the_missing_items(client: httpx.AsyncClient) -> None:
    await _put(client)
    check = (await client.get(f"{BASE}/handover")).json()
    assert check["ready"] is False
    ids = {i["id"]: i["status"] for i in check["items"]}
    assert set(ids) == {
        "ledger",
        "assumptions",
        "scenarios",
        "requirements",
        "parts",
        "manufacturing_context",
        "concept_assembly",
        "interfaces",
        "screening",
    }
    assert ids["assumptions"] == "missing" and ids["screening"] == "missing"
    res = await client.post(f"{BASE}/handover", json={"op_id": "h1"})
    assert res.status_code == 409
    items = res.json()["detail"]["items"]
    assert {i["id"] for i in items} == set(ids), "the whole checklist comes back"
    missing = {i["id"] for i in items if i["status"] == "missing"}
    assert {"assumptions", "screening", "concept_assembly"} <= missing


async def test_an_accepted_assumption_reads_as_assumed(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    await _make_ready(client, twin)
    await _event(
        client, "assumption_confirmed", {"id": "cover", "confirmed": False, "accepted": True}
    )
    items = {i["id"]: i for i in (await client.get(f"{BASE}/handover")).json()["items"]}
    assert items["assumptions"]["status"] == "assumed"


async def test_handover_freezes_the_studio_and_seeds_the_twin(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    await _make_ready(client, twin)
    check = (await client.get(f"{BASE}/handover")).json()
    assert check["ready"] is True, check
    res = await client.post(f"{BASE}/handover", json={"op_id": "h1"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["handed_over"] is True and body["revision"] == 1
    created = body["created"]
    assert "CS-IMAGINE@1" in created or any(c.startswith("CS-IMAGINE@") for c in created)
    assert any(c.startswith("MFC-PROJECT@") for c in created)
    assert any(c.startswith("CMP-") for c in created)
    assert "IFC-CAD-EDA@1" in created and "IFC-EDA-FIRMWARE@1" in created
    assert any(c.startswith("evidence:") for c in created)
    # Idempotent per op_id; a different op_id finds it already handed over.
    again = await client.post(f"{BASE}/handover", json={"op_id": "h1"})
    assert again.status_code == 200 and again.json()["created"] == created
    assert (await client.post(f"{BASE}/handover", json={"op_id": "h2"})).status_code == 409
    studio = (await client.get(f"{BASE}/studio")).json()
    assert studio["status"] == "handed_over" and studio["handover"]["op_id"] == "h1"
    # Read-only: no event, no new config.
    frozen = await _event(client, "choice", {"id": "th", "pick": "th-b"})
    assert frozen.status_code == 409
    assert (await client.put(f"{BASE}/studio", json={"config": full_config()})).status_code == 409


async def test_only_a_person_hands_over_or_reworks(client: httpx.AsyncClient) -> None:
    agent = {"X-MetaForge-Surface": "agent"}
    await _put(client)
    assert (
        await client.post(f"{BASE}/handover", json={"op_id": "h"}, headers=agent)
    ).status_code == 403
    assert (
        await client.post(f"{BASE}/rework", json={"op_id": "r", "reason": "x"}, headers=agent)
    ).status_code == 403


async def test_rework_opens_a_new_revision_and_stales_what_was_handed_over(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    choice = await _event(client, "choice", {"id": "th", "pick": "th-b"})
    decision_ref = choice.json()["mapped_to"][0]
    await _make_ready(client, twin)
    assert (await client.post(f"{BASE}/handover", json={"op_id": "h1"})).status_code == 200
    open_draft = await client.post(f"{BASE}/rework", json={"op_id": "r0", "reason": "x"})
    assert open_draft.status_code == 200
    body = open_draft.json()
    # The studio object, the same shape as GET /studio, plus what went stale.
    assert body["status"] == "draft" and body["revision"] == 2
    assert body["config_ref"] == "IMG-STUDIO@2" and body["config"]["product"]
    assert decision_ref in body["stale"]
    node = await twin.graph.get_node(UUID(decision_ref.split(":", 1)[1]))
    assert record_staleness(node.metadata) == "stale"  # type: ignore[union-attr]
    studio = (await client.get(f"{BASE}/studio")).json()
    assert studio["status"] == "draft" and studio["config_ref"] == "IMG-STUDIO@2"
    assert studio["handover"] is None
    # The same op_id is idempotent; a rework of an open studio is refused.
    again = await client.post(f"{BASE}/rework", json={"op_id": "r0", "reason": "x"})
    assert again.status_code == 200 and again.json()["revision"] == 2
    assert (
        await client.post(f"{BASE}/rework", json={"op_id": "r9", "reason": "x"})
    ).status_code == 409
    # And it can be edited again.
    assert (await _event(client, "choice", {"id": "th", "pick": "th-a"})).status_code == 200


async def test_an_agent_writes_the_config_over_mcp(service: ImagineService) -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    assert "twin.imagine_put_studio" not in TwinServer(twin=service.twin).tool_ids
    srv = TwinServer(twin=service.twin, imagine_studio=service)
    assert {
        "twin.imagine_get_studio",
        "twin.imagine_put_studio",
        "twin.imagine_handover_status",
    } <= set(srv.tool_ids)
    put = await srv.imagine_put_studio({"project_id": PROJECT, "config": full_config()})
    assert put["config_ref"] == "IMG-STUDIO@1" and put["status"] == "draft"
    got = await srv.imagine_get_studio({"project_id": PROJECT})
    assert got["revision"] == 1
    check = await srv.imagine_handover_status({"project_id": PROJECT})
    assert check["ready"] is False and len(check["items"]) == 9
    bad = base_config()
    bad["process"]["material"] = "unobtainium"
    with pytest.raises(ValueError, match="unobtainium"):
        await srv.imagine_put_studio({"project_id": PROJECT, "config": bad})
    with pytest.raises(ValueError, match="project_id"):
        await srv.imagine_get_studio({"project_id": "nope"})


async def test_the_dashboard_forwards_the_whole_page_write(client: httpx.AsyncClient) -> None:
    await _put(client)
    res = await client.post(
        f"{BASE}/events",
        json={
            "event_id": f"evt-{uuid4()}",
            "kind": "choice",
            "data": {"collection": "choices", "id": "th", "data": {"pick": "th-b", "at": "x"}},
        },
    )
    assert res.status_code == 200, res.text
    studio = (await client.get(f"{BASE}/studio")).json()
    assert studio["collections"]["choices"]["th"] == {"pick": "th-b", "at": "x", "id": "th"}
    assert set(studio["collections"]) == {
        "choices",
        "scenarios",
        "contextEdits",
        "assumptions",
        "requirements",
        "ledger",
        "screening",
    }


async def test_with_auth_on_the_engine_url_carries_a_signed_token(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.files import links
    from api_gateway.imagine import engine_token

    monkeypatch.setenv("METAFORGE_LINK_SECRET", "test-secret")
    links.reset_secret_cache()
    await _put(client)
    plain = (await client.get(f"{BASE}/studio")).json()["engine_url"]
    assert plain == f"/v1/twin/projects/{PROJECT}/imagine/engine"
    monkeypatch.setattr(engine_token, "auth_required", lambda: True)
    url = (await client.get(f"{BASE}/studio")).json()["engine_url"]
    assert url.startswith(f"/v1/twin/projects/{PROJECT}/imagine/engine?t=")
    assert (await client.get(url)).status_code == 200
    assert (await client.get(f"{BASE}/engine")).status_code == 401
    assert (await client.get(f"{BASE}/engine?t=forged.token")).status_code == 401
    other = engine_token.sign(str(uuid4()))
    assert (await client.get(f"{BASE}/engine?t={other}")).status_code == 401
    expired = engine_token.sign(PROJECT, now=0)
    assert (await client.get(f"{BASE}/engine?t={expired}")).status_code == 401
    # A signed download link is not an engine token, nor the other way round.
    link, _ = links.sign("download", PROJECT, lifetime_s=600)
    assert (await client.get(f"{BASE}/engine?t={link}")).status_code == 401
    with pytest.raises(links.LinkError):
        links.verify(url.split("t=", 1)[1])
    links.reset_secret_cache()


def test_the_auth_middleware_lets_only_the_engine_through() -> None:
    from api_gateway.auth.middleware import PUBLIC_GET_PATTERNS

    def public(path: str) -> bool:
        return any(p.match(path) for p in PUBLIC_GET_PATTERNS)

    assert public(f"/v1/twin/projects/{PROJECT}/imagine/engine")
    assert public(f"/v1/twin/projects/{PROJECT}/imagine/engine/assets/three.min.js")
    assert not public(f"/v1/twin/projects/{PROJECT}/imagine/studio")
    assert not public(f"/v1/twin/projects/{PROJECT}/imagine/engine/assets/../studio")
    assert not public("/v1/twin/projects/x/imagine/engine")


async def test_a_bad_project_id_is_refused(client: httpx.AsyncClient) -> None:
    res = await client.get("/v1/twin/projects/not-a-uuid/imagine/studio")
    assert res.status_code == 400


def test_full_config_is_a_copy() -> None:
    a, b = full_config(), full_config()
    a["product"]["name"] = "changed"
    assert b["product"]["name"] != "changed"
    assert copy.deepcopy(a) == a


# ── FORGE-824: an agent hands over for a person, only under delegation ────
#
# The handover and the rework are a person's decisions. With the gateway
# owner's METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY off (the default) the agent
# surface is refused exactly as before. With it on, an agent may make them,
# naming itself and the person it acts for, and the record says so.

FLAG = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
AGENT = {
    "X-MetaForge-Surface": "agent",
    "X-MetaForge-Agent": "claude-code",
    "X-MetaForge-On-Behalf-Of": "fidel",
}
_TOOLS = ("twin.imagine_handover", "twin.imagine_rework")


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FLAG, raising=False)


async def test_flag_off_the_agent_is_refused_even_with_its_headers(
    client: httpx.AsyncClient,
) -> None:
    await _put(client)
    res = await client.post(f"{BASE}/handover", json={"op_id": "h"}, headers=AGENT)
    assert res.status_code == 403
    res = await client.post(f"{BASE}/rework", json={"op_id": "r", "reason": "x"}, headers=AGENT)
    assert res.status_code == 403


@pytest.mark.parametrize("missing", ["X-MetaForge-Agent", "X-MetaForge-On-Behalf-Of"])
async def test_flag_on_without_the_identity_headers_is_422(
    missing: str, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    await _put(client)
    headers = {k: v for k, v in AGENT.items() if k != missing}
    res = await client.post(f"{BASE}/handover", json={"op_id": "h"}, headers=headers)
    assert res.status_code == 422, res.text
    assert missing in res.text
    res = await client.post(f"{BASE}/rework", json={"op_id": "r", "reason": "x"}, headers=headers)
    assert res.status_code == 422


async def test_flag_on_the_agent_hands_over_and_reworks_on_the_persons_behalf(
    client: httpx.AsyncClient,
    twin: InMemoryTwinAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    published: list[str] = []
    monkeypatch.setattr(
        ImagineService, "_publish", lambda self, pid, what, message: published.append(message)
    )
    await _put(client)
    await _make_ready(client, twin)
    res = await client.post(f"{BASE}/handover", json={"op_id": "h1"}, headers=AGENT)
    assert res.status_code == 200, res.text
    handover = (await client.get(f"{BASE}/studio")).json()["handover"]
    # Recorded as the agent's decision for that person, never as the person.
    assert handover["by"] == "agent:claude-code"
    assert handover["agent"] == "claude-code"
    assert handover["on_behalf_of"] == "fidel"
    # The operations log says the same.
    assert any("decided by agent claude-code on behalf of fidel" in m for m in published)
    res = await client.post(
        f"{BASE}/rework", json={"op_id": "r1", "reason": "taller"}, headers=AGENT
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "draft"
    assert "on behalf of fidel" in published[-1]


async def test_a_persons_handover_is_unchanged(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    await _put(client)
    await _make_ready(client, twin)
    assert (await client.post(f"{BASE}/handover", json={"op_id": "h1"})).status_code == 200
    handover = (await client.get(f"{BASE}/studio")).json()["handover"]
    assert handover["agent"] is None and handover["on_behalf_of"] is None
    assert not handover["by"].startswith("agent:")


# ── the MCP tools ────────────────────────────────────────────────────────


def test_the_tools_are_human_authority_and_never_auto_approved() -> None:
    for tool in _TOOLS:
        assert requires_human_authority(tool)
        assert never_auto_approved(tool)
        assert tool in DESTRUCTIVE


def _server(service: Any) -> Any:
    from tool_registry.tools.twin.adapter import TwinServer

    return TwinServer(twin=service.twin, imagine_studio=service)


async def test_the_tools_are_registered_with_the_studio(service: Any) -> None:
    assert set(_TOOLS) <= set(_server(service).tool_ids)


async def test_flag_off_the_tool_is_refused(service: Any, client: httpx.AsyncClient) -> None:
    await _put(client)
    srv = _server(service)
    with pytest.raises(ValueError, match="person's decision"):
        await srv.imagine_handover({"project_id": PROJECT, "op_id": "h", APPROVED_BY_ARG: "fidel"})
    with pytest.raises(ValueError, match="person's decision"):
        await srv.imagine_rework(
            {"project_id": PROJECT, "op_id": "r", "reason": "x", APPROVED_BY_ARG: "fidel"}
        )


async def test_the_tool_needs_the_approver_the_dispatcher_supplies(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    srv = _server(service)
    with pytest.raises(HumanAuthorityRequiredError):
        await srv.imagine_handover({"project_id": PROJECT, "op_id": "h"})
    # The model cannot name the person itself.
    with pytest.raises(Exception, match="approved_by"):
        await srv.imagine_handover({"project_id": PROJECT, "op_id": "h", "approved_by": "fidel"})


async def test_flag_on_the_tool_hands_over_for_the_approver(
    service: Any,
    client: httpx.AsyncClient,
    twin: InMemoryTwinAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _put(client)
    await _make_ready(client, twin)
    monkeypatch.setenv(FLAG, "true")
    srv = _server(service)
    ctx = McpCallContext(actor_id="agent:claude_code", client="claude-code")
    with with_context(ctx):
        out = await srv.imagine_handover(
            {"project_id": PROJECT, "op_id": "h1", APPROVED_BY_ARG: "user:fidel"}
        )
    assert out["handed_over"] is True
    handover = (await srv.imagine_get_studio({"project_id": PROJECT}))["handover"]
    assert handover["by"] == "agent:claude-code"
    assert handover["on_behalf_of"] == "user:fidel"


async def test_a_tool_handover_before_ready_names_what_is_missing(
    service: Any, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _put(client)
    monkeypatch.setenv(FLAG, "true")
    with pytest.raises(ValueError, match="Missing"):
        await _server(service).imagine_handover(
            {"project_id": PROJECT, "op_id": "h1", APPROVED_BY_ARG: "fidel"}
        )


# ── FORGE-827: an agent accepts assumptions and confirms scenarios for a person ──


def _inferred_config() -> dict[str, Any]:
    """The full config with its scenarios inferred, as an agent writes them."""
    cfg = full_config()
    cfg["scenarios"] = [{k: v for k, v in s.items() if k != "by"} for s in cfg["scenarios"]]
    return cfg


async def _ready_but_acceptance(client: httpx.AsyncClient, twin: InMemoryTwinAPI) -> None:
    """Everything the checklist needs, except the person's acceptances."""
    await _put(client, _inferred_config())
    cfg = full_config()
    for i, row in enumerate(cfg["programme"]["ledger"]):
        res = await _event(client, "ledger", {"id": f"l{i}", "index": i, "status": row["status"]})
        assert res.status_code == 200, res.text
    res = await _event(
        client,
        "screening",
        {"id": "latest", "engine": "formgraph@2.0.0", "rule_version": "x", "rules": []},
    )
    assert res.status_code == 200, res.text
    await _add_mesh(twin)


def _items(check: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in check["items"]}


def test_the_accept_tool_is_human_authority() -> None:
    assert requires_human_authority("twin.imagine_accept")
    assert never_auto_approved("twin.imagine_accept")


async def test_flag_off_the_accept_tool_is_refused(service: Any, client: httpx.AsyncClient) -> None:
    await _put(client, _inferred_config())
    with pytest.raises(ValueError, match="person's decision"):
        await _server(service).imagine_accept(
            {
                "project_id": PROJECT,
                "op_id": "a1",
                "assumptions": ["cover"],
                APPROVED_BY_ARG: "fidel",
            }
        )


async def test_the_accept_tool_needs_the_approver_the_dispatcher_supplies(
    service: Any, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _put(client, _inferred_config())
    monkeypatch.setenv(FLAG, "true")
    with pytest.raises(HumanAuthorityRequiredError):
        await _server(service).imagine_accept(
            {"project_id": PROJECT, "op_id": "a1", "assumptions": ["cover"]}
        )


async def test_an_agent_accepts_for_the_person_and_hands_over(
    service: Any,
    client: httpx.AsyncClient,
    twin: InMemoryTwinAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready_but_acceptance(client, twin)
    before = _items((await client.get(f"{BASE}/handover")).json())
    assert before["assumptions"]["status"] == "missing"
    assert before["scenarios"]["status"] == "missing"
    monkeypatch.setenv(FLAG, "true")
    srv = _server(service)
    scenarios = [s["id"] for s in _inferred_config()["scenarios"]]
    ctx = McpCallContext(actor_id="agent:x", agent="claude-code-e2e", client="claude-code 2")
    with with_context(ctx):
        out = await srv.imagine_accept(
            {
                "project_id": PROJECT,
                "op_id": "a1",
                "assumptions": ["cover", "coverage", "rail"],
                "scenarios": scenarios,
                APPROVED_BY_ARG: "fidel",
            }
        )
    assert out["agent"] == "claude-code-e2e" and out["on_behalf_of"] == "fidel"
    items = _items(out["checklist"])
    # Accepted, not confirmed: the assumptions stay assumptions.
    assert items["assumptions"]["status"] == "assumed"
    assert "accepted by agent claude-code-e2e on behalf of fidel" in items["assumptions"]["detail"]
    assert "not confirmed by a person" in items["assumptions"]["detail"]
    assert items["scenarios"]["status"] == "done"
    assert "accepted by agent claude-code-e2e on behalf of fidel" in items["scenarios"]["detail"]
    assert "not confirmed by a person" in items["scenarios"]["detail"]
    assert out["checklist"]["ready"] is True, items
    studio = (await client.get(f"{BASE}/studio")).json()
    assert studio["collections"]["assumptions"]["cover"].get("confirmed") is not True
    assert studio["collections"]["scenarios"]["knock"]["by"] == "agent"
    with with_context(ctx):
        handed = await srv.imagine_handover(
            {"project_id": PROJECT, "op_id": "h1", APPROVED_BY_ARG: "fidel"}
        )
    assert handed["handed_over"] is True


async def test_the_accept_tool_refuses_an_unknown_id(
    service: Any, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _put(client, _inferred_config())
    monkeypatch.setenv(FLAG, "true")
    with pytest.raises(ValueError, match="nope"):
        await _server(service).imagine_accept(
            {"project_id": PROJECT, "op_id": "a1", "scenarios": ["nope"], APPROVED_BY_ARG: "f"}
        )


async def test_rest_flag_off_an_agent_cannot_confirm_a_scenario(
    client: httpx.AsyncClient,
) -> None:
    await _put(client, _inferred_config())
    res = await client.post(
        f"{BASE}/events",
        json={"event_id": "s1", "kind": "scenario", "data": {"id": "knock", "confirmed": True}},
        headers=AGENT,
    )
    assert res.status_code == 403


async def test_rest_flag_on_without_the_person_is_422(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    await _put(client, _inferred_config())
    headers = {k: v for k, v in AGENT.items() if k != "X-MetaForge-On-Behalf-Of"}
    res = await client.post(
        f"{BASE}/events",
        json={"event_id": "s1", "kind": "scenario", "data": {"id": "knock", "confirmed": True}},
        headers=headers,
    )
    assert res.status_code == 422


async def test_rest_flag_on_the_agent_accepts_for_the_person(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "true")
    await _put(client, _inferred_config())
    res = await client.post(
        f"{BASE}/events",
        json={"event_id": "s1", "kind": "scenario", "data": {"id": "knock", "confirmed": True}},
        headers=AGENT,
    )
    assert res.status_code == 200, res.text
    res = await client.post(
        f"{BASE}/events",
        json={
            "event_id": "a1",
            "kind": "assumption_confirmed",
            "data": {"id": "cover", "confirmed": True},
        },
        headers=AGENT,
    )
    assert res.status_code == 200, res.text
    studio = (await client.get(f"{BASE}/studio")).json()
    knock = studio["collections"]["scenarios"]["knock"]
    assert knock["agent"] == "claude-code" and knock["on_behalf_of"] == "fidel"
    cover = studio["collections"]["assumptions"]["cover"]
    # An agent's "confirm" is an acceptance: never a person's statement.
    assert cover.get("confirmed") is not True and cover["accepted"] is True
    assert cover["on_behalf_of"] == "fidel"


async def test_a_browser_cannot_claim_an_agent_accepted(client: httpx.AsyncClient) -> None:
    await _put(client, _inferred_config())
    res = await _event(
        client,
        "assumption_confirmed",
        {"id": "cover", "accepted": True, "agent": "x", "on_behalf_of": "y"},
    )
    assert res.status_code == 200, res.text
    cover = (await client.get(f"{BASE}/studio")).json()["collections"]["assumptions"]["cover"]
    assert "agent" not in cover and "on_behalf_of" not in cover


async def test_an_agent_made_concept_mesh_lands_in_the_sessions_project(
    service: Any, client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    """The concept mesh path without a person: the checklist sees the agent's mesh."""
    from tool_registry.tools.twin.adapter import TwinServer

    seen: list[dict[str, Any]] = []

    async def generator(**call: Any) -> dict[str, Any]:
        seen.append(call)
        await _add_mesh(twin)  # what the real generator records for call["project_id"]
        return {"node_id": "n1", "task_id": "t1", "format": "glb"}

    await _put(client)
    srv = TwinServer(twin=twin, concept_mesh_generator=generator)
    with with_context(McpCallContext(project_id=UUID(PROJECT))):
        await srv.generate_concept_mesh({"prompt": "a desk lamp", "wait": True})
    assert seen[0]["project_id"] == PROJECT
    items = _items((await client.get(f"{BASE}/handover")).json())
    assert items["concept_assembly"]["status"] == "done"
    assert "concept mesh exists" in items["concept_assembly"]["detail"]


async def test_an_undelegated_agent_scenario_edit_is_not_the_persons(
    client: httpx.AsyncClient,
) -> None:
    await _put(client, _inferred_config())
    res = await client.post(
        f"{BASE}/events",
        json={"event_id": "s1", "kind": "scenario", "data": {"id": "knock", "text": "Dropped"}},
        headers={"X-MetaForge-Surface": "agent"},
    )
    assert res.status_code == 200, res.text
    items = _items((await client.get(f"{BASE}/handover")).json())
    assert "knock" in items["scenarios"]["detail"]


# -- validation (imagine-validation-api-spec.md) ----------------------------


async def test_validate_without_a_studio_is_404(client: httpx.AsyncClient) -> None:
    res = await client.post(f"{BASE}/validate", json={})
    assert res.status_code == 404


async def test_validate_names_who_clears_each_blocker(client: httpx.AsyncClient) -> None:
    await _put(client)
    res = await client.post(f"{BASE}/validate", json={})
    assert res.status_code == 200, res.text
    report = res.json()
    assert report["level"] in ("V0", "V1", "V2")
    assert report["verdict"] in ("needs_agent", "needs_person", "blocked_external")
    blockers = [
        f for f in report["findings"] if f["severity"] == "blocking" and f["status"] == "fail"
    ]
    assert blockers
    assert {f["resolver"] for f in blockers} <= {"agent", "person", "external"}
    assert any(f["check_id"] == "concept.mesh" and f["resolver"] == "external" for f in blockers)


async def test_validate_agrees_with_the_handover_checklist(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    await _make_ready(client, twin)
    checklist = (await client.get(f"{BASE}/handover")).json()
    report = (await client.get(f"{BASE}/validation")).json()
    assert checklist["ready"] is True
    assert report["verdict"] == "ready_for_handover"
    assert report["level"] == "V2"
    assert report["stale"] is False


async def test_validate_writes_nothing(client: httpx.AsyncClient) -> None:
    before = await _put(client)
    await client.post(f"{BASE}/validate", json={})
    await client.get(f"{BASE}/validation")
    after = (await client.get(f"{BASE}/studio")).json()
    assert after["revision"] == before["revision"]


async def test_an_invalid_config_is_a_422_whose_body_is_the_report(
    client: httpx.AsyncClient,
) -> None:
    await _put(client)
    res = await client.post(f"{BASE}/validate", json={"config": {"not": "a product"}})
    assert res.status_code == 422
    body = res.json()["detail"]
    assert body["verdict"] == "invalid"
    assert body["level"] == "V0"
    assert any(f["gate"] == "config_valid" for f in body["findings"])


async def test_a_scope_re_checks_only_what_it_names(client: httpx.AsyncClient) -> None:
    await _put(client)
    full = (await client.post(f"{BASE}/validate", json={})).json()
    scope = [{"kind": "studio", "id": "concept_mesh"}]
    part = (await client.post(f"{BASE}/validate", json={"scope": scope})).json()
    assert [f["check_id"] for f in part["findings"]] == ["concept.mesh"]
    assert len(part["findings"]) < len(full["findings"])


async def test_a_scope_naming_nothing_is_a_422_with_the_bad_ref(
    client: httpx.AsyncClient,
) -> None:
    await _put(client)
    bad = {"kind": "part", "id": "no-such-part"}
    res = await client.post(f"{BASE}/validate", json={"scope": [bad]})
    assert res.status_code == 422
    assert res.json()["detail"]["bad_refs"][0]["id"] == "no-such-part"


async def test_only_the_head_revision_can_be_validated(client: httpx.AsyncClient) -> None:
    await _put(client)
    res = await client.post(f"{BASE}/validate", json={"revision": 99})
    assert res.status_code == 422


async def test_a_handed_over_studio_refuses_a_supplied_config(
    client: httpx.AsyncClient, twin: InMemoryTwinAPI
) -> None:
    await _put(client)
    await _make_ready(client, twin)
    done = await client.post(f"{BASE}/handover", json={"op_id": "h1"})
    assert done.status_code == 200, done.text
    res = await client.post(f"{BASE}/validate", json={"config": full_config()})
    assert res.status_code == 409
    assert (await client.post(f"{BASE}/validate", json={})).status_code == 200
