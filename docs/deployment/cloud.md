# Gateway authentication (MetaForge Cloud)

The gateway runs in one of two modes. Local is the default and is unchanged from
every previous release; cloud adds accounts on top rather than replacing
anything.

| Mode | `METAFORGE_AUTH_MODE` | Who can call it |
|---|---|---|
| Local | `off` (default) | Anything that can reach the port |
| Cloud | `supabase` | Callers holding a valid Supabase access token |

## Local mode

Nothing to do. Do not set `METAFORGE_AUTH_MODE`, and the gateway behaves exactly
as before: no middleware is installed, no token is required, nothing is slower.

This mode has no concept of a user, so it remains true that anything able to
reach the port can read and change your digital twin. Keep it on a private
network — Tailscale, or an authenticating proxy such as Cloudflare Access — and
off the open internet. See [Vercel deployment](vercel.md) for the HTTPS and
mixed-content side of that.

## Cloud mode

### Configuration

| Variable | Required | Meaning |
|---|---|---|
| `METAFORGE_AUTH_MODE` | yes | Set to `supabase` |
| `METAFORGE_SUPABASE_URL` | yes¹ | Project URL, e.g. `https://abcdefgh.supabase.co`. JWKS and issuer are derived from it |
| `METAFORGE_CORS_ORIGINS` | yes | Comma-separated exact origins, e.g. `https://app.metaforge.uk` |
| `METAFORGE_SUPABASE_JWKS_URL` | no | Override the derived JWKS URL |
| `METAFORGE_SUPABASE_JWT_SECRET` | no¹ | Legacy symmetric signing key. Mutually exclusive with the two above |
| `METAFORGE_SUPABASE_JWT_AUDIENCE` | no | Defaults to `authenticated` |

¹ Supply either `METAFORGE_SUPABASE_URL` (recommended) or
`METAFORGE_SUPABASE_JWT_SECRET`, not both.

```bash
METAFORGE_AUTH_MODE=supabase
METAFORGE_SUPABASE_URL=https://abcdefgh.supabase.co
METAFORGE_CORS_ORIGINS=https://app.metaforge.uk
```

### Asymmetric keys are strongly preferred

With `METAFORGE_SUPABASE_URL` the gateway fetches public keys from the project's
JWKS document and can only *verify* tokens. With `METAFORGE_SUPABASE_JWT_SECRET`
it holds a symmetric key that can also **mint** them — anyone who reads that
environment variable can forge any user. It is supported because older Supabase
projects still use it, and the gateway logs a warning on every start when it is
in use. Migrate the project to asymmetric signing keys.

## The gateway refuses to start rather than run open

This is deliberate and is the main safety property of the design. A gateway
that was meant to be authenticated and silently is not is worse than one that
never started, because nothing alerts you.

`METAFORGE_AUTH_MODE=supabase` will **fail at startup**, not warn and continue,
when:

- no verification method is configured — no project URL, no JWKS URL, no secret
- both a JWKS URL and a symmetric secret are set, which select contradictory
  signature schemes
- `METAFORGE_SUPABASE_URL` is not `https`
- `METAFORGE_CORS_ORIGINS` is `*`. A wildcard origin with credentialed requests
  would let any website call the gateway using a signed-in user's token

An unrecognised mode (`METAFORGE_AUTH_MODE=supbase`) is also fatal, so a typo
cannot quietly mean `off`.

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

Enforcement is middleware, not a per-route dependency, so a route added later is
protected by default without its author doing anything. That is the opposite of
the pattern elsewhere in the codebase, where protection is opt-in and easy to
forget.

Callers present the token as a bearer credential:

```
Authorization: Bearer <supabase-access-token>
```

## Responses

| Status | Meaning |
|---|---|
| 401 | Missing, malformed, expired, wrongly-signed, or wrong-audience token. Carries `WWW-Authenticate: Bearer` |
| 503 | The gateway could not reach Supabase to verify. **Not** a credential problem |

The 401/503 split matters. Returning 401 when the identity provider is
unreachable would tell users their credentials are bad when the fault is ours.
If a previously-fetched JWKS document is cached, a provider blip degrades to
serving slightly stale keys rather than failing every request.

## Not yet covered

Authentication is not authorisation. This release verifies *who* is calling; it
does not yet scope what they may see. Accounts, memberships and per-account
project isolation are the next phase, as is authentication on the MCP surface —
the sidecar has its own separate scheme, documented in
[MCP Spec](../mcp_spec.md).
