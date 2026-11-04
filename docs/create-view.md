# The Create view

Create is the dashboard's nontechnical view of a project. Anyone can describe a
product in their own words, watch it take shape, choose how to make it, check
what comes out and improve it, without opening the engineering tools:

**Describe → Clarify → Design → Check → Approve → Make → Inspect → Improve or accept**

It is a view, not a separate product. A creation *is* a project: its
conversation is the project's chat thread, its designs are the project's work
products in the digital twin, and its decisions are the project's approvals.
The **Create / Engineering** switch in the topbar moves between the two views
of the same records, so nothing is copied and nothing is lost on the way.

## Where it lives

| | |
|---|---|
| Dashboard | `/create` (start or reopen a creation), `/create/:projectId` (one creation) |
| Gateway | `/v1/create/{project_id}/...` (`api_gateway/create/`) |
| Records | SQLite at `~/.metaforge/create.db` (`METAFORGE_CREATE_STORE_PATH`) |
| Reused | `/v1/projects`, `/v1/chat`, `/v1/twin`, `/v1/approvals`, `/v1/uploads`, `/v1/library/machines`, `/v1/manufacture/release` |

The gateway stores only what the Create journey adds on top of the project:
the **brief**, the **manufacturing route**, the **inspection plan**, the
**builds** (manufacturing attempts with their evidence, results and
acceptance) and a **history** of every change.

## The screen

- **Product stage** (centre). The newest dimensioned model, or the newest
  concept when there is no model yet, with a badge that says which it is:
  *Engineering model · dimensioned* or *Concept · illustrative, not a
  manufacturable model*. An attractive image is never presented as a model
  that can be made. The revision on screen is named, earlier results sit in a
  strip below, and *Discuss this* starts a message about the shown item.
  While a pass is running the last finished result stays on screen.
- **Conversation** (left). The project's chat thread. Each message says what
  it is: a *Question* and its *Discussion*, a *Change request* and its
  *Proposed change · awaiting your decision*, or a *Design pass* and what it
  *executed with tools*. The composer grows with the draft (no height cap, no
  inner scrollbar), shrinks as text is removed, re-measures when its width
  changes, and takes pasted or dropped files.
- **Where it stands** (right). The readiness state, what it means, the next
  action and each blocker with the action that clears it.
- **Brief, Decisions, Make, Inspect, History** tabs. The open tab is in the
  address (`?panel=make`), so back, forward and direct links work.
- **Journey rail** (top) showing the step the creation is on.

## Conversation and instructions

The composer has three intents:

| Intent | What is sent | What can happen |
|---|---|---|
| Ask | no tools | Discussion only; the agent may not claim it changed anything |
| Change | `twin.propose_change` | A proposal that waits in Decisions until a person approves it |
| Design | every harness tool | A design pass; writes are still held by the approval policy |

Instructions are sent one at a time. Anything sent while a pass is running is
shown as *Queued* and goes next; the one being worked on reads *Being
applied*. A send that fails stays in the conversation with *Retry*, *Edit*
(back into the composer) and *Discard*, so a failure never loses the text.

The draft, the chosen intent, attached references and queued instructions
live in a store outside the page (`store/create-store.ts`). Switching to the
Engineering view and back finds them as they were, and an instruction already
sent keeps running while the person is elsewhere. The draft survives a
reload; an instruction caught mid-send by a reload comes back as *not sent*,
with a note to check the conversation before retrying.

Replies are rendered with [generative UI](generative-ui.md): the agent can
answer with a comparison of options, a question to click an answer to, a
request for a measurement, figures against their limits or a proposed change
to approve in place, and the text itself is Markdown.

References (photos, sketches, documents, design files) upload the moment they
are attached, through `/v1/uploads` with the dashboard's own approval, and
each shows *Uploading*, *Added*, *Not supported* (with the reason) or
*Failed*. Added ones are listed in the brief's `references`.

## The brief

The first request becomes the brief's *Intended use*, in the person's words,
and the first message asks the agent to state its assumptions and at most
three short questions. The brief then holds:

| Item | Records |
|---|---|
| Intended use | The problem and who uses it |
| Size and fit | Dimensions and available space |
| Appearance | Style, colour and finish |
| Materials | Preferences and constraints |
| Design budget, Making budget | Kept separate: generation and compute versus manufacturing |
| Quantity | Prototype, one-off or repeat production |
| How you can make it | Machines available, supplier preferences |
| Success criteria | What a good result must achieve, one per line |
| How it will be checked | How the physical result is evaluated |

Each item is *Confirmed*, an *Assumption* (proposed, with a Confirm button)
or *Open*. Only the first open questions are shown, so there is no
questionnaire before work can start.

**Measurements** carry an explicit unit and say whether they are
*User-confirmed*, *Estimated* or *Unconfirmed*, and where they came from
(typed, marked on a photo, sketch or model, with the known dimension that set
the scale). A dimension marked *Critical* must be user-confirmed before a
build can be released. A camera picture alone is never treated as a
calibrated measurement.

The brief saves itself as it is edited. A save that lands after a change made
elsewhere (the Engineering view, an agent, another tab) is refused with
`409` and the newer record, and the person chooses *Load the latest* or
*Keep my version*.

## Readiness

Readiness is derived on every read, never stored, from the creation's records
plus the project's work products and pending approvals, so an Engineering
change that affects it shows up in Create at once with the reason.

| State | When |
|---|---|
| Concept | No dimensioned model yet (images and sketches are concepts) |
| Design in development | A model exists, but a work product is failing a check or a decision is waiting |
| Checked design | Checks recorded, nothing waiting; next is choosing how to make it |
| Ready for making | Route chosen, inspection plan set, critical dimensions confirmed, a build prepared |
| Released for making | A person released one exact revision |
| Build completed | Making has ended |
| Accepted product | Every required check passed and a person accepted the build |

A finished design run is not readiness: it does not mean the product is
manufacturable, safe for its use, or right for every process.

## Decisions

The Decisions tab lists the project's pending approvals in plain words, each
labelled with the kind of commitment it is, so a design approval is never
mistaken for spending or moving a machine: *Design approval*, *Spending*,
*External file sharing*, *Physical machine action*, *Adding files* or *Process
checkpoint*. The buttons are exactly the decisions the gateway allows for that
item, a reason is asked for where the gateway requires one, and findings and
*Full evidence in Engineering* are a click away. The approval rules are the
gateway's own ([Approvals](approvals.md)); Create adds none.

Releasing a build and accepting it are Create's own decisions, on the Make and
Inspect tabs.

## Making it

Three routes: **Make at home**, **Use my workshop** and **Have it made**. The
Make tab suggests a process from the brief and the model, says why (a metal
part suggests CNC machining, a single plastic part FDM printing), and allows
any other. Home and workshop builds pick a registered machine from the machine
library; a saved machine is not proof it is ready. For an external maker,
quotes, orders, file sharing and payment each go through their own connector
and approval; without a connector, *Download making files* exports STL or STEP
through `/v1/manufacture/release`.

A **build** is one manufacturing attempt of one exact revision (work product
and version). It is prepared, then **released by a person** after confirming
the machine, material and work area (or the files and the manufacturer's
requirements). Release copies the inspection plan into the build. From then
on the build's instructions are fixed: editing a released build is refused,
and a change needs a new build. Changing the route cancels a build that was
only prepared. Releasing does not start a machine: sending a job stays with
`/v1/library/jobs` and its own approval.

Build status moves *Released → Being made → Making finished*, or *Failed* or
*Cancelled*. Estimates (time, material, cost) are shown with their basis, or
the build says there is none yet.

## Inspection and acceptance

The **inspection plan** is set before making. The Inspect tab can turn the
success criteria into one check per line to start from. Each check records the
feature, the acceptance condition or tolerance, the evidence needed, the method
(camera observation, calibrated measurement, machine telemetry, operator
confirmation, test or instrument), who reviews it (the person, or an agent
allowed to evaluate it) and whether failing it blocks acceptance.

Evidence comes from the optional **camera** (pick a camera, live preview,
positioning guidance, resolution, snapshots), from uploaded photos, or from
measurements and notes. Nothing is recorded continuously: only the snapshots
taken are kept, as files in the project. A camera that is refused or missing
falls back to uploading photos. Leaving the tab ends the camera session; it is
started again on purpose.

*Ask MetaForge to compare the evidence with the design* sends the agent the
checks and the evidence, asking for an observation, a confidence and what is
uncertain for each, and for another view, measurement or operator check where
it needs one. Each check reads **Passed**, **Failed**, **Inconclusive** or
**Awaiting review**. An agent may record a finding, but a check reviewed by a
person can only be passed by a person (`403` otherwise).

Making finished is not acceptance. **Accept the build** is refused until every
required check has passed; checks that are not required and are still open are
written into the decision as limitations. A rejected or failed build offers
*Describe the correction*, which puts the failed checks into a change request
in the same conversation, so the next design pass and the next build stay in
the same creation.

## Creations

`/create` starts a creation from a prompt, an example, or a photo, sketch or
design file, and lists the existing ones (any project can be opened in
Create). Each can be renamed, duplicated (brief, route and inspection plan are
copied; builds are not), archived and restored, or deleted. Deleting explains
what goes (the creation, its brief, builds and inspection records, and its
links to files; the files stay in the twin), says that orders already placed
with a manufacturer are not cancelled, and is refused while a build is
released or being made: deleting never stops a machine.

## Presentation mode

**Present** fills the screen with the creation and adds a caption bar that
steps through the eight journey steps (arrow keys or the buttons; Esc leaves),
opening the tab that tells each step's story. In the offline sample workspace
(`?demo=1`) the sample project is a complete creation with a delivered build
waiting for its last check, so the whole loop can be shown without a gateway.

## Gateway API

All under `/v1/create/{project_id}`. Every write accepts `expected_version`
and answers `409` with the current record when it is stale. Writes return the
creation and its readiness.

| Method and path | Does |
|---|---|
| `GET /` | The creation (an empty one for a project never opened in Create) and its readiness |
| `PUT /brief` | Replace the brief |
| `PUT /route` | Choose the route, process, machine, material, manufacturer |
| `PUT /inspection-plan` | Replace the inspection plan |
| `POST /builds` | Prepare a build of a revision |
| `PATCH /builds/{id}` | Change a build that is only prepared |
| `POST /builds/{id}/release` | Release for making (a person only) |
| `POST /builds/{id}/status` | Record making started, finished, failed or cancelled |
| `POST /builds/{id}/evidence` | Add a photo, measurement, telemetry reading or note |
| `PUT /builds/{id}/results/{check_id}` | Record a check's result |
| `POST /builds/{id}/accept` | Accept or reject a finished build (a person only) |
| `POST /duplicate` | Copy into a new project |
| `DELETE /` | Remove the Create records (refused while a build is active) |

"A person only" means a caller sending `X-MetaForge-Surface: agent` is
refused with `403`. The full schemas are in the
[gateway API reference](reference/gateway-api.md).

## Not yet

- Readiness is read by polling every five seconds, not pushed.
- Marking a dimension directly on a photo or the model, and calibrated camera
  measurement, are not built; measurements are typed, with their source.
- Comparing two revisions side by side is in the Engineering view's revision
  tools, not on the Create stage.
- Finding manufacturers, quotes, orders and payment need a connector; the Make
  tab says so rather than imitating one.
