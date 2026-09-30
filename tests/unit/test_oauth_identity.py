"""Who signed in, and whether the server can prove it (FORGE-330).

A3 is "OAuth 2.1 sign-in with **per-user identity**; local bearer for
stdio". The OAuth server worked -- DCR, PKCE, tokens -- but every login
produced the same actor, ``oauth:web``, because the login is one shared
secret. So a session timeline said "oauth:web" for an entire team.

Two separate things, and conflating them is the bug this closes:

* **Who was at the keyboard.** Now recorded, from a name typed at login.
* **Whether the server established it.** It did not, and must not claim
  to: one secret shared by a team proves someone on the team, never which
  one -- exactly like the static API key, which was already reported as
  identifying nobody.
"""

from __future__ import annotations

import uuid

import pytest

from mcp_core.auth import AuthPosture
from mcp_core.context import McpCallContext, context_from_headers
from metaforge.mcp.oauth import OAuthConfig, OAuthProvider, _actor_for


def _challenge(verifier: str) -> str:
    """BASE64URL(SHA256(verifier)) with no padding, per RFC 7636."""
    import base64
    import hashlib

    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


# ---------------------------------------------------------------------------
# The operator label
# ---------------------------------------------------------------------------


def test_a_typed_name_becomes_the_actor() -> None:
    assert _actor_for("ana", "oauth:web") == "user:ana"


def test_no_name_falls_back_to_the_configured_actor() -> None:
    assert _actor_for("   ", "oauth:web") == "oauth:web"
    assert _actor_for(None, "oauth:web") == "oauth:web"


def test_a_name_cannot_forge_a_log_line() -> None:
    """This ends up in structured logs and session records. A name
    carrying newlines or separators would let a caller write entries that
    look like somebody else's."""
    actor = _actor_for("evil\nactor: admin; rm -rf /", "oauth:web")
    assert "\n" not in actor
    assert actor == "user:evilactoradminrm-rf"


def test_a_very_long_name_is_bounded() -> None:
    assert len(_actor_for("x" * 500, "oauth:web")) <= len("user:") + 64


# ---------------------------------------------------------------------------
# It survives the code -> token exchange
# ---------------------------------------------------------------------------


def _provider() -> OAuthProvider:
    return OAuthProvider(OAuthConfig(login_secret="open-sesame"))


def test_the_operator_reaches_the_access_token() -> None:
    """The point of the feature: a timeline that says who, not the
    deployment's one configured label."""
    oauth = _provider()
    client = oauth.register_client({"redirect_uris": ["http://localhost/cb"]})
    verifier = "a" * 64
    code = oauth.issue_code(
        oauth.validate_authorize(
            client_id=client["client_id"],
            redirect_uri="http://localhost/cb",
            response_type="code",
            code_challenge=_challenge(verifier),
            code_challenge_method="S256",
            scope=None,
        ),
        "http://localhost/cb",
        _challenge(verifier),
        None,
        operator="ana",
    )
    tokens = oauth.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client["client_id"],
            "redirect_uri": "http://localhost/cb",
            "code_verifier": verifier,
        }
    )
    assert oauth.validate_token(str(tokens["access_token"])) == "user:ana"


def test_no_operator_keeps_the_old_behaviour() -> None:
    """A client that never shows the login form (or a deployment that does
    not ask) still gets a working token."""
    oauth = _provider()
    client = oauth.register_client({"redirect_uris": ["http://localhost/cb"]})
    verifier = "b" * 64
    code = oauth.issue_code(
        oauth.validate_authorize(
            client_id=client["client_id"],
            redirect_uri="http://localhost/cb",
            response_type="code",
            code_challenge=_challenge(verifier),
            code_challenge_method="S256",
            scope=None,
        ),
        "http://localhost/cb",
        _challenge(verifier),
        None,
    )
    tokens = oauth.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client["client_id"],
            "redirect_uri": "http://localhost/cb",
            "code_verifier": verifier,
        }
    )
    assert oauth.validate_token(str(tokens["access_token"])) == "oauth:web"


# ---------------------------------------------------------------------------
# What the server will and will not claim
# ---------------------------------------------------------------------------


def test_the_shared_secret_login_does_not_claim_to_identify_anyone() -> None:
    assert OAuthConfig(login_secret="s").verified_identity is False


def test_an_actor_alone_is_not_an_identity() -> None:
    """The flag used to be "the field is not the default", so a client
    setting X-MetaForge-Actor was recorded as verified."""
    ctx = context_from_headers({"X-MetaForge-Actor": "user:ceo"})
    assert ctx.actor_id == "user:ceo"
    assert ctx.actor_is_attributable is False


def test_a_server_established_actor_is_attributable() -> None:
    ctx = McpCallContext(session_id=uuid.uuid4(), actor_id="user:ana", actor_verified=True)
    assert ctx.actor_is_attributable is True


def test_the_unattributed_sentinel_never_counts() -> None:
    """Even if something sets the flag: there is no one to attribute to."""
    ctx = McpCallContext(session_id=uuid.uuid4(), actor_verified=True)
    assert ctx.actor_is_attributable is False


@pytest.mark.parametrize(
    ("posture", "identifies"),
    [
        (AuthPosture(api_key=True), False),
        (AuthPosture(oauth=True), False),
        (AuthPosture(oauth=True, identifies_caller=True), True),
    ],
)
def test_only_a_real_identity_provider_identifies(posture: AuthPosture, identifies: bool) -> None:
    assert posture.report()["identifies_caller"] is identifies
