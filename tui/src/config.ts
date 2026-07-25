/**
 * Reads the same client config the Python `forge` CLI writes
 * (~/.forge/config.json), so the TUI shares gateway/provider/model/mode.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface ForgeConfig {
  gateway_url: string;
  provider?: string;
  model?: string;
  mode?: string;
}

const DEFAULTS: ForgeConfig = {
  gateway_url: "http://localhost:8000",
  mode: "ask",
};

export function configPath(): string {
  return join(homedir(), ".forge", "config.json");
}

export function loadConfig(): ForgeConfig {
  try {
    const raw = readFileSync(configPath(), "utf8");
    return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<ForgeConfig>) };
  } catch {
    return { ...DEFAULTS };
  }
}
