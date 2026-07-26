/**
 * Reads the same client config the Python `forge` CLI writes
 * (~/.forge/config.json), so the TUI shares gateway/provider/model/mode.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

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

export function saveConfig(cfg: ForgeConfig): void {
  const p = configPath();
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, `${JSON.stringify(cfg, null, 2)}\n`);
}

/** Set one config key (gateway_url|provider|model|mode) and persist. */
export function setConfigValue(key: string, value: string): ForgeConfig {
  const cfg = loadConfig();
  (cfg as unknown as Record<string, string>)[key] = value;
  saveConfig(cfg);
  return cfg;
}
