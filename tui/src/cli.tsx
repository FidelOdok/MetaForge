#!/usr/bin/env node
import { render } from "ink";
import { App } from "./App.js";
import { BUILD } from "./build-info.js";

const args = process.argv.slice(2);

if (args.includes("--version") || args.includes("-v")) {
  const stamp = BUILD.date ? `${BUILD.commit}, ${BUILD.date}` : BUILD.commit;
  console.log(`forge-tui ${BUILD.version} (${stamp})`);
  process.exit(0);
}

if (args.includes("--help") || args.includes("-h")) {
  console.log(
    [
      "forge-tui — MetaForge terminal UI (Ink)",
      "",
      "Usage: forge-tui",
      "",
      "  ^T  chat        streaming assistant + tool-call trace",
      "  ^R  runs        gated design-flow timeline + approve/reject",
      "  ^N  new         launch a design-flow run",
      "  ^B  twin        browse projects → work products",
      "  Esc/^C  quit",
      "",
      "Config: ~/.forge/config.json (gateway_url, provider, model, mode)",
    ].join("\n"),
  );
  process.exit(0);
}

render(<App />);
