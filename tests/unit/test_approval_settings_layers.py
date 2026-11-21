"""Approval settings in three layers: deployment, account, project (FORGE-904).

Each layer can only narrow the one above it: the mode a project may use, and
whether agents may decide human-authority steps and releases for a person.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.approvals.delegation import delegation_allowed
from api_gateway.approvals.policy import (
    AccountPolicy,
    AccountPolicyStore,
    ApprovalPolicy,
    PolicyStore,
    effective_mode,
    get_account_policy_store,
    get_policy_store,
    reset_account_policy_store,
    reset_policy_store,
)
from api_gateway.approvals.policy_routes import account_router
from api_gateway.approvals.policy_routes import router as policy_router
from twin_core.library.viewer import library_viewer

PROJECT = "p-layers"
HUMAN = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
RELEASE = "METAFORGE_ALLOW_AGENT_RELEASE"


@pytest.fixture(autouse=True)
def stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.setenv(HUMAN, "true")
    monkeypatch.setenv(RELEASE, "true")
    reset_policy_store(PolicyStore())
    reset_account_policy_store(AccountPolicyStore())
    yield
    reset_policy_store(None)
    reset_account_policy_store(None)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(policy_router)
    app.include_router(account_router)
    return TestClient(app)


# ── the mode ──────────────────────────────────────────────────────────────


async def test_the_accounts_ceiling_holds_a_project_below_it() -> None:
    await get_policy_store().put(ApprovalPolicy(project_id=PROJECT, mode="auto_clean"))
    assert effective_mode(await get_policy_store().get(PROJECT)) == "auto_clean"
    await get_account_policy_store().put(AccountPolicy(account="", max_mode="advise"))
    assert effective_mode(await get_policy_store().get(PROJECT)) == "advise"


async def test_the_callers_account_applies_and_else_the_projects_recorded_one() -> None:
    await get_account_policy_store().put(AccountPolicy(account="acct-a", max_mode="off"))
    await get_policy_store().put(ApprovalPolicy(project_id=PROJECT, mode="advise"))
    with library_viewer("acct-a"):
        assert effective_mode(await get_policy_store().get(PROJECT)) == "off"
    # A worker has no account of its own: the project's recorded account applies.
    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="advise", account="acct-a")
    )
    assert effective_mode(await get_policy_store().get(PROJECT)) == "off"


def test_a_project_cannot_choose_a_mode_above_its_account(client: TestClient) -> None:
    assert client.put("/v1/account/approval-policy", json={"max_mode": "advise"}).status_code == 200
    res = client.put(f"/v1/projects/{PROJECT}/approval-policy", json={"mode": "auto_clean"})
    assert res.status_code == 422 and "account's limit" in res.json()["detail"]
    view = client.put(f"/v1/projects/{PROJECT}/approval-policy", json={"mode": "advise"}).json()
    assert view["max_mode"] == "advise" and view["account_max_mode"] == "advise"


def test_lowering_the_account_later_shows_which_layer_holds_the_project(
    client: TestClient,
) -> None:
    client.put(f"/v1/projects/{PROJECT}/approval-policy", json={"mode": "auto_clean"})
    client.put("/v1/account/approval-policy", json={"max_mode": "advise"})
    view = client.get(f"/v1/projects/{PROJECT}/approval-policy").json()
    assert view["policy"]["mode"] == "auto_clean"
    assert view["effective_mode"] == "advise" and view["limited_by"] == "account"


def test_an_account_cannot_rise_above_the_deployment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "advise")
    res = client.put("/v1/account/approval-policy", json={"max_mode": "auto"})
    assert res.status_code == 422 and "deployment's ceiling" in res.json()["detail"]


def test_the_views_show_the_deployments_limits(client: TestClient) -> None:
    account = client.get("/v1/account/approval-policy").json()
    deployment = account["deployment"]
    assert deployment["max_mode"] == "auto"
    assert deployment["allow_human_authority"] is True
    caps = {c["tool"]: c for c in deployment["hourly_caps"]}
    assert caps["twin.render_concept_image"]["per_hour"] == 20
    assert caps["cadquery.execute_script"]["set_by"] == "METAFORGE_AUTO_APPROVE_SCRIPT_PER_HOUR"
    project = client.get(f"/v1/projects/{PROJECT}/approval-policy").json()
    assert project["deployment"]["max_mode"] == "auto"
    assert project["delegation"]["release"]["allowed"] is True


def test_an_agent_cannot_change_the_account_settings(client: TestClient) -> None:
    res = client.put(
        "/v1/account/approval-policy",
        json={"max_mode": "off"},
        headers={"X-MetaForge-Surface": "agent"},
    )
    assert res.status_code == 403


# ── delegation ─────────────────────────────────────────────────────────────


async def test_delegation_needs_every_layer_and_names_the_one_that_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = await delegation_allowed("human_authority", PROJECT)
    assert allowed.allowed and allowed.decided_by == "deployment"

    await get_policy_store().put(ApprovalPolicy(project_id=PROJECT, allow_human_authority=False))
    refused = await delegation_allowed("human_authority", PROJECT)
    assert not refused.allowed and refused.decided_by == "project"
    # The release switch is separate.
    assert (await delegation_allowed("release", PROJECT)).allowed

    await get_account_policy_store().put(AccountPolicy(account="", allow_release=False))
    release = await delegation_allowed("release", PROJECT)
    assert not release.allowed and release.decided_by == "account"

    monkeypatch.setenv(RELEASE, "false")
    off = await delegation_allowed("release", PROJECT)
    assert not off.allowed and off.decided_by == "deployment"
    assert "METAFORGE_ALLOW_AGENT_RELEASE" in off.reason


async def test_a_project_turning_it_on_cannot_reach_past_the_account() -> None:
    await get_account_policy_store().put(AccountPolicy(account="", allow_human_authority=False))
    await get_policy_store().put(ApprovalPolicy(project_id=PROJECT, allow_human_authority=True))
    verdict = await delegation_allowed("human_authority", PROJECT)
    assert not verdict.allowed and verdict.decided_by == "account"


def test_an_account_cannot_turn_on_what_the_deployment_has_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HUMAN, "false")
    res = client.put("/v1/account/approval-policy", json={"allow_human_authority": True})
    assert res.status_code == 422
    view = client.put("/v1/account/approval-policy", json={"allow_release": False}).json()
    assert view["allow_human_authority"] is False and view["allow_release"] is False


def test_the_project_view_says_which_layer_refused(client: TestClient) -> None:
    client.put("/v1/account/approval-policy", json={"allow_human_authority": False})
    view = client.get(f"/v1/projects/{PROJECT}/approval-policy").json()
    human = view["delegation"]["human_authority"]
    assert human["allowed"] is False and human["decided_by"] == "account"


async def test_the_agent_refusal_on_an_approval_follows_the_project() -> None:
    from api_gateway.approvals.schemas import ApprovalItem
    from api_gateway.approvals.service import HUMAN_AUTHORITY_KINDS, for_caller

    kind = sorted(HUMAN_AUTHORITY_KINDS)[0]
    item = ApprovalItem.model_validate(
        {
            "id": f"{kind}:x",
            "kind": kind,
            "title": "t",
            "status": "pending",
            "project_id": PROJECT,
            "allowed_decisions": ["approve", "reject"],
            "decidable": True,
        }
    )
    assert (await for_caller(item, "agent")).decidable is True
    await get_policy_store().put(ApprovalPolicy(project_id=PROJECT, allow_human_authority=False))
    held = await for_caller(item, "agent")
    assert held.decidable is False
    assert "project's approval policy" in str(held.not_decidable_reason)


# ── the sidecar says whose account a call is for ──────────────────────────


async def test_the_sidecar_forwards_the_account_with_a_policy_question() -> None:
    from metaforge.mcp.remote_approvals import RemoteApprovalLedger
    from twin_core.library.viewer import VIEW_HEADER

    seen: list[dict[str, Any]] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"approved": False})

    client = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    ledger = RemoteApprovalLedger("http://gw.test", client=client)
    with library_viewer("acct-a"):
        await ledger._post("http://gw.test/v1/chat/tool_approvals/policy", {})
    assert seen[-1].get(VIEW_HEADER.lower())
