---
title: Gateway authentication
# The page moved from cloud.md when the hosted provider moved to its own
# repository. The URL is pinned so that every link published
# before the move still resolves -- docs-site/tests/routes.test.mjs checks it.
slug: /deployment/cloud
---

# Gateway authentication

The gateway runs in one of two shapes. Local is the default and is unchanged
from every release before authentication existed; an authenticated gateway adds
accounts on top rather than replacing anything.

| Shape | `METAFORGE_AUTH_MODE` | Who can call it |
|---|---|---|
| Local | `off` (default) | Anything that can reach the port |
| Authenticated | the name of an installed provider | Callers holding a credential that provider accepts |

## Local mode

Nothing to do. Do not set `METAFORGE_AUTH_MODE`, and the gateway behaves exactly
as before: no middleware is installed, no credential is required, nothing is
slower, and no crypto stack is imported.

This mode has no concept of a user, so it remains true that anything able to
reach the port can read and change your digital twin. Keep it on a private
network — Tailscale, or an authenticating proxy such as Cloudflare Access — and
off the open internet. See [Vercel deployment](vercel.md) for the HTTPS and
mixed-content side of that.

## Authentication is a plug-in

MetaForge is open core. This repository contains the half of authentication
that belongs to every gateway — the shape of a verified caller, default-deny
enforcement, and the refusal to start when authentication was asked for and
cannot be delivered. It contains no way to check a credential, because that is
specific to an identity provider.

A provider is a separately-installed Python distribution that registers itself
on the `metaforge.auth` entry-point group:

```toml
# pyproject.toml of the provider distribution
[project.entry-points."metaforge.auth"]
supabase = "metaforge_cloud.auth:SupabaseAuthProvider"
```

`METAFORGE_AUTH_MODE` then names the registration to use. The hosted provider,
which verifies Supabase-issued access tokens and carries the accounts
control-plane schema, ships with **MetaForge Cloud** and is not part of this
repository.

### Writing one

Implement `api_gateway.auth.AuthProvider` — three members, and the two failure
classes are the whole contract:

```python
from api_gateway.auth import AuthUnavailable, InvalidToken, Principal


class MyAuthProvider:
    name = "my-provider"

    async def verify(self, token: str) -> Principal:
        """Return the caller, or raise.

        InvalidToken     -- checked, and the answer is no.       HTTP 401
        AuthUnavailable  -- no verdict could be reached.         HTTP 503
        """
        ...

    async def aclose(self) -> None:
        """Release anything held, such as an HTTP client."""
```

There is deliberately no third outcome. A provider that returns a `Principal`
for a token it could not verify defeats the entire arrangement, so the
interface gives it nowhere to express "probably fine".

The provider reads its own configuration when it is constructed, and raises
`AuthConfigurationError` if that configuration is missing or contradictory.
Because construction happens inside `create_app`, a misconfigured provider
stops the process rather than surfacing as a 500 on the first real request.

## The gateway refuses to start rather than run open

This is deliberate and is the main safety property of the design. A gateway
that was meant to be authenticated and silently is not is worse than one that
never started, because nothing alerts you.

A non-`off` `METAFORGE_AUTH_MODE` will **fail at startup**, not warn and
continue, when:

- no provider is registered under that name. This is the common one, and it is
  not only a typo: it is what a gateway image built without the provider
  package looks like. The message names both what was asked for and what is
  actually installed
- the registered entry point cannot be imported, or resolves to something with
  no `verify()`
- the provider itself rejects its configuration
- `METAFORGE_CORS_ORIGINS` is `*`. A wildcard origin with credentialed requests
  would let any website call the gateway using a signed-in user's token

None of these fall back to `off`.

## Confirming what a running gateway is doing

Read it from the process, not from the config you think you deployed:

```console
$ curl -s https://gateway.example.com/health | jq .auth_mode
"supabase"
```

`/health` is intentionally public — liveness probes cannot carry tokens, and
this field is how you verify the gateway is protected. It exposes no project
data.

## What is protected

Everything except a short allow-list: `/health`, `/docs`,
`/docs/oauth2-redirect`, `/redoc`, `/openapi.json`, and `OPTIONS` preflight
requests.

Two kinds of route carry a credential of their own instead of a bearer token
and check it themselves: signed links under `/v1/links/`, and the Imagine
engine page (`GET /v1/twin/projects/{id}/imagine/engine` and its
`/engine/assets/{name}`), which an iframe loads and which takes the
short-lived signed `t` that `GET .../imagine/studio` puts in `engine_url`.
See [The Imagine stage](../imagine-stage.md#authentication-and-the-engine-url).

Enforcement is middleware, not a per-route dependency, so a route added later is
protected by default without its author doing anything. That is the opposite of
the pattern elsewhere in the codebase, where protection is opt-in and easy to
forget.

Callers present the credential as a bearer token:

```
Authorization: Bearer <access-token>
```

## Several accounts on one gateway

Authentication says *who* is calling. A gateway shared by several accounts
must also confine each caller to their own projects. A provider that can
answer that implements a second, optional protocol,
`ProjectAuthorizer` (`api_gateway/auth/scope.py`):

```python
async def project_scope(self, principal) -> ProjectScope: ...
async def project_created(self, principal, project_id: str) -> None: ...
```

With it installed, the middleware matches every request to its route before
the route runs, and the route's shape decides how the project is found:

| Route shape | Example | On a shared gateway |
|---|---|---|
| `{project_id}` in the path | `GET /v1/twin/projects/{project_id}/…` | answered only for the caller's projects |
| `project_id` / `projectId` query parameter | `GET /v1/knowledge/search?project_id=…` | parameter required, then checked |
| `project_id` / `projectId` in the JSON body | `POST /v1/runs` | field required, then checked |
| `GET /v1/projects` | | filtered to the caller's projects |
| `POST /v1/projects` | | the new project is registered to its creator via `project_created`; if that fails the project is deleted again and the call returns 503 |
| `GET` under `/v1/library/`, `/v1/examples`, `/v1/features` | shared reference data | readable; writes refused |
| anything else | | **refused with 403** |

A project outside the caller's scope gets **404**, the same as one that does
not exist, so ids cannot be probed. A provider that cannot reach its answer
raises `AuthUnavailable` and the caller gets **503**, never an empty scope.

The last row is the point: routes are classified from their signatures, so a
new route that takes `project_id` is confined automatically, and a new route
that does not is unavailable on a shared gateway until someone decides how it
is scoped. It is never answered across accounts by default.

A scope marked `unrestricted` is for the deployment's own services (a worker
calling back into the gateway), never a person: it skips all of the above.

A provider without `ProjectAuthorizer` changes nothing, and a local gateway
(`auth_mode: off`) never loads any of this.

## Signing in to MCP

The MCP endpoint (`metaforge.mcp` over HTTP) runs its own OAuth 2.1 server,
because claude.ai and Claude Code sign in to it directly. Its sign-in page has
to establish who is signing in:

| Gateway | MCP sign-in | Token bound to |
|---|---|---|
| `auth_mode: off`, `METAFORGE_OAUTH_LOGIN_SECRET` set | one shared secret, plus a name typed as a label | a label (`user:<name>`), not a verified person |
| a provider that implements `PasswordLogin` | the person's own email and password, checked by the provider | the verified subject (`sub:<subject>`) |

`PasswordLogin` (`api_gateway/auth/login.py`) is a second optional provider
capability, next to `ProjectAuthorizer`:

```python
async def verify_password(self, email: str, password: str) -> Principal: ...
```

Wrong credentials raise `InvalidToken` (the page says so, 401); an
unreachable identity provider raises `AuthUnavailable` (503, never "wrong
password"). With it installed the shared secret is refused even if set:
anyone holding it would act outside every account. A typed name can never
pass for a verified subject, because the label is stripped of `:`.

Binding a token to a person is what a project scope needs; applying that
scope to MCP tool calls is a separate step. Until it is in place, an MCP
endpoint on a gateway shared by several accounts must not be offered to more
than one of them.

## Responses

| Status | Meaning |
|---|---|
| 401 | Missing, malformed, or rejected credential. Carries `WWW-Authenticate: Bearer` |
| 503 | The provider could not reach a verdict. **Not** a credential problem |

The 401/503 split matters. Returning 401 when the identity provider is
unreachable would tell users their credentials are bad when the fault is ours.

## The dashboard side

The dashboard has the same seam. `metaforge:auth` resolves to
`dashboard/src/auth/none.ts` — no client, which is what this repository ships
and what every local install wants. A build that has a provider points
`METAFORGE_AUTH_MODULE` at a module exporting an `AuthClient`
(`dashboard/src/auth/types.ts`).

Whether a signed-in user is *required* is not a build-time decision either way:
`AuthGate` reads `auth_mode` from the gateway's own `/health` response. A
dashboard with no client, pointed at a gateway that wants tokens, says so on
screen rather than looping on 401s.
