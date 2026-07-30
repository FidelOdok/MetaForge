/**
 * ChatGPT/Codex OAuth (PKCE loopback) for `forge auth login --provider openai-codex`.
 *
 * A TypeScript port of `orchestrator/harness/providers/codex_login.py`: same
 * Codex public client, same `localhost:1455` callback, same authorize/token
 * endpoints. Runs the browser flow on THIS machine and returns the tokens; the
 * caller pushes them to the gateway (which stores them where the Codex adapter
 * reads them). Nothing is written to disk here.
 */
import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";

const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize";
const TOKEN_URL = "https://auth.openai.com/oauth/token";
const REDIRECT_PORT = 1455;
const REDIRECT_URI = `http://localhost:${REDIRECT_PORT}/auth/callback`;
const SCOPE = "openid profile email offline_access";
const ORIGINATOR = "codex_cli_rs";
const LOGIN_TIMEOUT_MS = 5 * 60_000;

/** The subset of the token response the gateway needs (auth.json `tokens` shape). */
export interface CodexTokens {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
}

const b64url = (buf: Buffer): string => buf.toString("base64url");

function pkce(): { verifier: string; challenge: string } {
  const verifier = b64url(randomBytes(64));
  const challenge = b64url(createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

function authorizeUrl(challenge: string, state: string): string {
  const p = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
    scope: SCOPE,
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
    id_token_add_organizations: "true",
    codex_cli_simplified_flow: "true",
    originator: ORIGINATOR,
  });
  return `${AUTHORIZE_URL}?${p.toString()}`;
}

function openBrowser(url: string): void {
  const [cmd, args] =
    process.platform === "darwin"
      ? ["open", [url]]
      : process.platform === "win32"
        ? ["cmd", ["/c", "start", "", url]]
        : ["xdg-open", [url]];
  try {
    spawn(cmd as string, args as string[], { stdio: "ignore", detached: true }).unref();
  } catch {
    /* headless — the caller prints the URL to open manually */
  }
}

async function exchangeCode(code: string, verifier: string): Promise<CodexTokens> {
  // Form-encoded, matching the Python transport (`httpx.post(url, data=...)`).
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: CLIENT_ID,
    code,
    redirect_uri: REDIRECT_URI,
    code_verifier: verifier,
  });
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`token exchange failed: ${res.status} ${detail.slice(0, 200)}`);
  }
  const j = (await res.json()) as Record<string, unknown>;
  if (!j.access_token) throw new Error("token response had no access_token");
  return {
    access_token: String(j.access_token),
    refresh_token: j.refresh_token ? String(j.refresh_token) : undefined,
    id_token: j.id_token ? String(j.id_token) : undefined,
  };
}

/**
 * Run the browser loopback login and resolve the Codex tokens.
 * `onUrl` receives the authorize URL (print it for a headless/`--no-browser` run).
 */
export function loginChatGPT(opts: { open?: boolean; onUrl?: (url: string) => void } = {}): Promise<CodexTokens> {
  const { verifier, challenge } = pkce();
  const state = b64url(randomBytes(16));
  const url = authorizeUrl(challenge, state);

  return new Promise<CodexTokens>((resolve, reject) => {
    const server = createServer((req, res) => {
      const u = new URL(req.url ?? "/", `http://localhost:${REDIRECT_PORT}`);
      if (u.pathname !== "/auth/callback") {
        res.writeHead(404);
        res.end();
        return;
      }
      const code = u.searchParams.get("code");
      const returnedState = u.searchParams.get("state");
      const finish = (ok: boolean, msg: string) => {
        res.writeHead(ok ? 200 : 400, { "Content-Type": "text/html" });
        res.end(`<html><body style="font-family:sans-serif;text-align:center;padding-top:3rem">
          <h2>${ok ? "✓ Authorization successful" : "Login failed"}</h2>
          <p>${msg}</p></body></html>`);
        server.close();
      };
      if (!code || returnedState !== state) {
        finish(false, "state mismatch or missing code");
        reject(new Error("OAuth callback: state mismatch or missing code"));
        return;
      }
      finish(true, "You can close this window and return to the terminal.");
      exchangeCode(code, verifier).then(resolve, reject);
    });

    server.on("error", reject);
    server.listen(REDIRECT_PORT, "127.0.0.1", () => {
      opts.onUrl?.(url);
      if (opts.open !== false) openBrowser(url);
    });
    setTimeout(() => {
      server.close();
      reject(new Error("login timed out after 5 minutes"));
    }, LOGIN_TIMEOUT_MS).unref();
  });
}
