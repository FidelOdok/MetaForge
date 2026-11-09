"""The gateway's client for the verification service (rendering side only)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from api_gateway.imagine import verification_client as vc


def client(handler: Any, token: str | None = "tok") -> vc.VerificationClient:
    return vc.VerificationClient(
        "http://verify.test/", token, transport=httpx.MockTransport(handler)
    )


async def test_it_posts_the_request_and_returns_the_report_untouched() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"verdict": "needs_agent", "findings": []})

    report = await client(handler).validate({"revision": {"twin_id": "p"}})
    assert report == {"verdict": "needs_agent", "findings": []}
    assert seen["url"] == "http://verify.test/v1/validations"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"revision": {"twin_id": "p"}}


async def test_no_token_sends_no_credential() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    await client(handler, token=None).validate({})
    assert seen["auth"] is None


async def test_an_unreachable_service_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(vc.VerificationUnavailable, match="could not reach"):
        await client(handler).validate({})


async def test_a_timeout_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(vc.VerificationUnavailable):
        await client(handler).validate({})


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_credential_is_unavailable_not_a_bug(status: int) -> None:
    with pytest.raises(vc.VerificationUnavailable, match="credential"):
        await client(lambda r: httpx.Response(status)).validate({})


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_server_failure_is_unavailable(status: int) -> None:
    with pytest.raises(vc.VerificationUnavailable, match=str(status)):
        await client(lambda r: httpx.Response(status)).validate({})


async def test_a_422_is_a_refusal_of_our_request_not_an_outage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": [{"msg": "unique"}]})

    with pytest.raises(vc.VerificationRefused) as exc:
        await client(handler).validate({})
    assert exc.value.detail == [{"msg": "unique"}]


async def test_an_answer_that_is_not_json_is_unavailable() -> None:
    with pytest.raises(vc.VerificationUnavailable, match="JSON"):
        await client(lambda r: httpx.Response(200, text="<html>")).validate({})


async def test_an_answer_that_is_not_an_object_is_unavailable() -> None:
    with pytest.raises(vc.VerificationUnavailable, match="shape"):
        await client(lambda r: httpx.Response(200, json=[1])).validate({})


def test_from_env_is_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(vc.URL_ENV, raising=False)
    assert vc.from_env() is None
    monkeypatch.setenv(vc.URL_ENV, "   ")
    assert vc.from_env() is None


def test_from_env_reads_the_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(vc.URL_ENV, "http://verify.test/")
    monkeypatch.setenv(vc.TOKEN_ENV, "secret")
    built = vc.from_env()
    assert built is not None
    assert built.base_url == "http://verify.test"
    assert built._token == "secret"
