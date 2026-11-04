# Generative UI in chat

An agent reply in MetaForge is prose plus, optionally, a short list of typed
**blocks** that the dashboard renders as components. When a comparison, a
question with a few answers or a set of figures against their limits is
clearer than a paragraph, the agent shows it as one. The same renderer serves
both chats: the Create conversation and the Engineering (digital twin) chat.

Blocks are data, never markup. Each kind is a closed schema with bounded
fields, so a reply cannot inject HTML or script, and a malformed block is
refused when the agent sends it rather than reaching a browser.

## Block kinds

| Kind | Shows | The person can |
|---|---|---|
| `choice` | A question with two to eight answers (`multiple` for several) | Click an answer; it is sent as their reply |
| `comparison` | Two to five options side by side, with criteria, pros, cons and a recommendation | Read and compare |
| `measurement_request` | A dimension the agent needs, its unit, why, and whether it is critical | Enter the value; it is sent as their reply |
| `table` | Up to 8 columns and 50 rows | Read |
| `metrics` | Figures, each optionally against a ceiling (`max`) or floor (`min`), with a meter | See at a glance what is over or under |
| `callout` | An `info`, `warning`, `risk` or `success` note | Read |
| `proposal` | A proposed design change, by `change_id` | Approve or reject it in place |
| `part` | A work product in the twin, by node id | Show it on the stage (Create) or select it in the graph (Engineering) |

A reply carries at most 8 blocks. Figures carry a `basis`: `illustrative`,
`estimate`, `calculated`, `measured` or `verified`, shown as a tag, so an
estimate is never presented as a measurement.

Interactions are ordinary, visible acts by the person. An answer from a
`choice` or `measurement_request` is sent as their next message, as a
question with no tools, so answering never starts a design pass. A
`proposal` is decided through the same approval API as the Changes tab
(`POST /v1/assistant/proposals/{change_id}/decide`). Only the latest reply's
blocks accept input; older ones show what was answered.

## How a reply gets blocks

1. **`chat.render_ui`.** A native harness tool registered on every chat turn
   (`api_gateway/chat/harness_backend.py`, `make_render_ui_tool`). The agent
   calls it with `{"blocks": [...]}`. The handler validates them against
   `api_gateway/chat/ui_blocks.py`; an invalid block comes back as a tool error
   naming the field, so the agent can correct it within the turn.
2. **Filed proposals.** When a turn calls `twin.propose_change` successfully,
   a `proposal` block for that change is attached automatically, unless the
   agent already showed it. The person decides where the change is mentioned
   instead of hunting for it in a separate tab.

The turn's blocks are stored on the agent's reply and returned on every
message as `blocks` (empty for a message without any). See
`MessageResponse` in the [gateway API reference](reference/gateway-api.md).

## Answer style

The chat system prompt (`ANSWER_STYLE` in
`api_gateway/chat/harness_backend.py`) asks for the answer first, in one or
two plain sentences, a reply usually under 120 words, everyday words, no
Markdown tables and no narration of which tools were called. When
`chat.render_ui` is registered it also tells the model to show options,
figures, questions, measurements and warnings as blocks rather than prose.
This is guidance to the model, not a hard limit: a long reply is still shown
in full.

## What the agent did

The Engineering chat shows a turn's progress as a few plain steps ("Read the
design record", "Ran a stress check", "Waiting for your approval: proposed a
design change"), each marked in progress, done, did not work or needs you
(`dashboard/src/components/chat/activity.ts`). A tool's start and its result
are one line; thinking and typing events are not shown. The raw stream
events are still available under *Raw events* when response detail is set
to *verbose*, and the list is hidden at *concise*.

## Activity overlay

Longer work is not shown as chat text at all. Each design-flow run, tool job
and Create chat turn is an operation, and both the Create and Engineering
views show the primary one in the activity overlay
(`dashboard/src/components/activity/ActivityOverlay.tsx`). Its rules:

- Status words, stages and milestones come from the gateway's operation
  events, never from a client timer. An event older than the last one applied
  to its operation is dropped, a finished operation never returns to running,
  and an older revision never replaces a newer one.
- The animated drawing is drawn from a fixed set of typed scenes
  (`components/activity/scenes/`), one per domain. They use CSS stroke-dash,
  transform and opacity only, with no filters, scripts or event handlers, and
  never render SVG produced by a model. A scene is labelled *Illustrative
  activity* until a real artifact preview replaces it.
- A percentage needs a measured total; there is no ETA.
- Decisions use the existing approval card, and cancel or retry appear only
  when the operation offers them.
- In Engineering the overlay also lists lanes per domain with stage ids,
  revisions, severity and the raw event lines, under *Technical detail*.

The Engineering chat's own stream is read with the same fetch-based SSE
reader as the operations stream (`dashboard/src/lib/sse.ts`), so it carries
a bearer token to a gateway behind an authentication provider.

## Storage

`chat_messages.blocks` is a JSON column. The gateway adds it to an existing
Postgres database at startup (`ADDITIVE_COLUMNS` in `api_gateway/db/models.py`,
an idempotent `ADD COLUMN IF NOT EXISTS`), because `create_all` never alters a
table that already exists. Blocks are validated on the way in and again on
read; a stored block that no longer validates is dropped from the response
with a warning rather than failing the whole thread.

## Rendering

`dashboard/src/components/chat/MessageBody.tsx` renders one message: the text
as Markdown (paragraphs, headings, lists, code, bold, italic and http(s)
links; built as React elements, never as HTML), then the blocks
(`UiBlocks.tsx`). The person's own messages are shown exactly as typed. A
block kind the dashboard does not know yet is reported as content it cannot
show, never silently dropped. Styles are in `styles/chat-blocks.css` and
follow the light and dark themes.

The sample workspace's seeded conversation uses metrics, comparison,
proposal, choice, callout, part and measurement blocks, so the offline demo
shows them without a gateway or a model.

## Not yet

- Blocks arrive with the finished reply. The live stream carries the
  `chat.render_ui` call as an `agent.step`, but there is no separate block
  event to render them before the turn ends.
- A measurement given through a block is sent as a message. It is not yet
  written into the Create brief's measurements, which still take their
  confirmation there.
- No chart block beyond `metrics`, and no image block.
