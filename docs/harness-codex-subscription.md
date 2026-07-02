# Using a ChatGPT subscription with the harness (openai-codex)

Drive the MetaForge harness on a **ChatGPT Plus/Pro subscription** — no API key
— via the `openai-codex` provider (MET-550). This mirrors how Hermes/OpenClaw
use a subscription: the subscription itself pays for model calls.

> **Caveats.** The Codex backend is an **undocumented API that can change
> without notice**, and subscription-driven programmatic use is a **gray area**
> under OpenAI's terms. Use a real API key or OpenRouter if you need a
> supported/stable path.

## 1. Log in once with the official Codex CLI

The harness reuses the official CLI's credentials rather than reimplementing
the OAuth flow:

```bash
npx @openai/codex login
```

Sign in with the account that has your ChatGPT subscription. This writes
`~/.codex/auth.json` (or `$CODEX_HOME/auth.json`). The harness reads the access
token + account id from it and refreshes the OAuth token automatically.

## 2a. Use it from the dashboard chat

```bash
export METAFORGE_CHAT_HARNESS=on
export METAFORGE_LLM_PROVIDER=openai-codex
export METAFORGE_LLM_MODEL=gpt-5-codex      # or another Codex-served model
uvicorn api_gateway.server:app --port 8000
```

Chat in the dashboard — no `METAFORGE_LLM_API_KEY` is needed; the provider
resolves to the CODEX family and calls the Codex backend with your
subscription credentials.

## 2b. Use it from the library

```python
import asyncio
from orchestrator.harness import build_agent_runtime
from orchestrator.harness.policy import ModelPolicy
from orchestrator.harness.react import run_react
from orchestrator.harness.providers import load_provider_config

cfg = load_provider_config(
    {"roles": {"generator": [{"provider": "openai-codex", "model": "gpt-5-codex"}]}}
)
rt = build_agent_runtime(cfg).runtime
print(asyncio.run(run_react(rt, ModelPolicy(rt), "say hi in 3 words")).output)
```

`default_invoke` routes `openai-codex` to `codex_invoke`, which loads/refreshes
credentials from `~/.codex/auth.json` and calls the Responses API.

## How it works

- `orchestrator/harness/providers/codex_auth.py` — reads `~/.codex/auth.json`
  (tolerant to field variants; account id decoded from the id_token JWT),
  detects expiry from the access-token `exp` claim, and refreshes via
  `https://auth.openai.com/oauth/token` (client id `app_EMoamEEZ73f0CkXaXp7hrann`).
- `codex_invoke` (in `adapters.py`) — builds an authed client
  (`Authorization: Bearer <access_token>`, `chatgpt-account-id: <account_id>`,
  base URL `https://chatgpt.com/backend-api/codex`) and calls the **Responses
  API**.
- Registered as provider id `openai-codex` (alias `codex`), CODEX family.

## Troubleshooting

- **`no Codex credentials found`** — run `npx @openai/codex login` first, or set
  `CODEX_HOME` to the directory containing `auth.json`.
- **HTTP 400/401** — the subscription is the subsidy; ensure the logged-in
  account has an active ChatGPT Plus/Pro plan, and that the token refreshed
  (delete `~/.codex/auth.json` and re-login if it's stale/revoked).
