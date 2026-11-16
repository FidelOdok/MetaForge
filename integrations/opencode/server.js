// MetaForge server plugin for opencode V2.
//
// Shipped in integrations/opencode by scripts/build_integrations.py, which
// also writes `metaforge-index.json` beside it. Installed with
//
//   opencode plugin add 'github:FidelOdok/MetaForge#main::path:integrations/opencode'
//
// it gives what install.sh gives: the `metaforge` MCP server, the
// /metaforge/<name> commands, the skills, the subagents and the working rules.
// Every file is read relative to this module, never the working directory.
//
// No import of @opencode/plugin: V2 reads `id` and `setup` from the default
// export, and Plugin.define only type-checks them. Without the dependency the
// package installs with nothing to fetch from npm.

import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = dirname(fileURLToPath(import.meta.url))
const INDEX = JSON.parse(readFileSync(join(ROOT, "metaforge-index.json"), "utf8"))

function read(relative) {
  return readFileSync(join(ROOT, relative), "utf8")
}

// The markdown after a leading `---` frontmatter block, trimmed.
export function body(text) {
  const normal = text.replace(/\r\n/g, "\n")
  if (normal.startsWith("---\n")) {
    const end = normal.indexOf("\n---\n", 4)
    if (end !== -1) return normal.slice(end + 5).trim()
  }
  return normal.trim()
}

// What opencode does for a markdown command: $ARGUMENTS is the whole
// argument string, and with no placeholder the arguments follow the template
// after a blank line.
export function expand(template, args) {
  const text = (args ?? "").trim()
  if (template.includes("$ARGUMENTS")) return template.replaceAll("$ARGUMENTS", text)
  return text ? `${template}\n\n${text}` : template
}

export default {
  id: "metaforge",
  async setup(ctx) {
    const server = INDEX.mcp.metaforge
    // A `metaforge` server the person configured wins over the package's.
    await ctx.mcp.transform((editor) => {
      if (editor.get("metaforge") === undefined) editor.set("metaforge", { ...server })
    })

    const commands = INDEX.commands.map((command) => ({
      ...command,
      template: body(read(command.file)),
    }))
    await ctx.command.transform((editor) => {
      for (const command of commands) {
        editor.add({
          name: command.name,
          description: command.description,
          execute: async ({ sessionID, prompt, delivery }) => {
            await ctx.session.prompt({
              ...prompt,
              sessionID,
              text: expand(command.template, prompt?.text),
              delivery,
            })
          },
        })
      }
    })

    const skills = INDEX.skills.map((skill) => ({
      id: skill.id,
      name: skill.name,
      description: skill.description,
      path: join(ROOT, skill.file),
      content: body(read(skill.file)),
    }))
    await ctx.skill.transform((editor) => {
      for (const skill of skills) if (editor.get(skill.id) === undefined) editor.add({ ...skill })
    })

    // AgentEditor has no `add`; `update` on an id that does not exist creates
    // it, which is how opencode's own config loader adds agents.
    const agents = INDEX.agents.map((agent) => ({ ...agent, system: body(read(agent.file)) }))
    await ctx.agent.transform((editor) => {
      for (const agent of agents) {
        if (editor.get(agent.id) !== undefined) continue
        editor.update(agent.id, (info) => {
          info.description = agent.description
          info.mode = "subagent"
          info.system = agent.system
        })
      }
    })

    // The working rules, as an instruction entry on every new session.
    const rules = read(INDEX.rules)
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({
          types: ["session.created"],
          signal: controller.signal,
        })) {
          const sessionID = event?.data?.sessionID ?? event?.data?.info?.id ?? event?.data?.id
          if (!sessionID) continue
          await ctx.session.instructions.entry
            .put({ sessionID, key: "metaforge-rules", value: rules })
            .catch((error) => console.error("metaforge: could not attach the rules", error))
        }
      } catch (error) {
        if (!controller.signal.aborted) console.error("metaforge: session events stopped", error)
      }
    })()
    return () => controller.abort()
  },
}
