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

// -- Approvals ----------------------------------------------------------------
//
// opencode 2.3 creates every MCP elicitation form in the session "global"
// instead of the session whose tool call raised it, and its TUI only shows
// forms for the session you are in, so MetaForge's approval prompts are never
// seen (reported upstream). /metaforge/approve and /metaforge/decline let the
// person answer them. Typed by the person, they answer in code: nothing here
// goes through the model, which cannot decide an approval.
//
// A held call's form exists only while the call waits (about 20 s): opencode
// keeps no stream open between calls. With nothing waiting, the command asks
// the agent to make the identical call again; the server puts the same
// approval to the person again while that call waits, and the person types
// the command once more to answer it.

// MetaForge's own pending forms from a forms list (opencode's API returns
// either the array or {data: [...]}).
export function metaforgeForms(listed, server = "metaforge") {
  const forms = Array.isArray(listed) ? listed : (listed?.data ?? [])
  return forms.filter(
    (form) => form?.metadata?.kind === "mcp-elicitation" && form?.metadata?.server === server,
  )
}

// The answer for a decision, from the form's own fields: the boolean is the
// decision, a text field takes the person's note when there is one.
export function answerFor(form, approve, note) {
  const answer = {}
  for (const field of form?.fields ?? []) {
    if (field?.type === "boolean") answer[field.key] = approve
    else if (note && (field?.type === "string" || field?.type === "text")) answer[field.key] = note
  }
  return answer
}

// Whether a form is the one the person named: the approval id, or
// the text the person typed after the command, must appear in its message.
export function formMatches(form, wanted) {
  if (!wanted) return true
  const message = String(form?.metadata?.message ?? "") + " " + String(form?.title ?? "")
  return message.includes(wanted)
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

    // /metaforge/approve and /metaforge/decline (see the note on Approvals).
    const mcpName = "metaforge"

    // opencode files MCP elicitation forms under the session "global"; a
    // plugin lists them per session (there is no top-level form client).
    // Requests about the session "global" name the project in this header,
    // as opencode's own client does; without it the server answers for its
    // own working directory, where there are no forms.
    const globalScope = () => ({
      headers: { "x-opencode-directory": encodeURIComponent(ctx.location?.directory ?? "") },
    })

    async function pendingForms() {
      const listed = await Promise.resolve()
        .then(() => ctx.session.form.list({ sessionID: "global" }, globalScope()))
        .catch((error) => {
          console.error("metaforge: could not list forms", error)
          return []
        })
      return metaforgeForms(listed, mcpName)
    }

    async function answer(form, decision) {
      await ctx.session.form.reply(
        {
          sessionID: form.sessionID,
          formID: form.id,
          answer: answerFor(form, decision.approve, decision.note),
        },
        form.sessionID === "global" ? globalScope() : undefined,
      )
      console.error(`metaforge: ${decision.approve ? "approved" : "declined"} ${form.id}`)
    }

    function decisionCommand(approve) {
      return {
        name: approve ? "metaforge/approve" : "metaforge/decline",
        description: approve
          ? "Approve the MetaForge write waiting for you (opencode does not show its prompt)"
          : "Decline the MetaForge write waiting for you (opencode does not show its prompt)",
        execute: async ({ sessionID, prompt, delivery }) => {
          const wanted = (prompt?.text ?? "").trim()
          const decision = { approve, wanted, note: approve ? "" : wanted }
          const waiting = (await pendingForms()).filter((form) => formMatches(form, approve ? wanted : ""))
          const verb = approve ? "approved" : "declined"
          if (waiting.length > 0) {
            await answer(waiting[0], decision)
            await ctx.session.prompt({
              ...prompt,
              sessionID,
              delivery,
              text:
                `I ${verb} the MetaForge approval that was waiting. ` +
                (approve
                  ? "If that call has not run yet, make the identical call again now; it runs once."
                  : "Do not retry that call."),
            })
            return
          }
          await ctx.session.prompt({
            ...prompt,
            sessionID,
            delivery,
            text:
              `I want to ${approve ? "approve" : "decline"} the MetaForge call waiting for my approval, ` +
              "but its prompt is no longer open. Make that identical call again now (same tool, same " +
              `arguments): it is put to me again while it waits, and I will type /metaforge/${approve ? "approve" : "decline"} ` +
              "then. Do not decide it yourself, and do not make any other call that writes until it is answered.",
          })
        },
      }
    }
    await ctx.command.transform((editor) => {
      editor.add(decisionCommand(true))
      editor.add(decisionCommand(false))
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
