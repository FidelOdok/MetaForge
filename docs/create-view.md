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

## Activity overlay

Work that takes time (a design-flow run, a tool job, a Create chat turn) is
shown as an **operation** in a small activity card. The card has its own row
directly above the stage, and the stage starts below it, so the compact card
never covers the stage's toolbars or panels (the sketch tools, *Generate*, the
editable sketch), the composer or the conversation. It can be expanded into a
larger, slightly translucent overlay that lays over the top right of the
stage, and minimised again with its button or **Escape**. Minimising or
closing it never cancels anything. The card comes before the conversation and
the stage in the page, so the keyboard reaches its controls straight after
the page header.

- **One at a time.** The card shows one primary operation: a decision waiting
  for the person first, then a failure, then live work. Any other live
  operations collapse into a counter such as *+2*.
- **Plain status.** The status is one of *Queued*, *Working*, *Checking*,
  *Waiting for a decision*, *Ready*, *Needs attention* or *Unable to
  complete*, with the current step and the elapsed time. The time runs from
  when the operation left the queue, and is left out rather than guessed when
  that is not known. Expanded, it also
  shows what is done so far, parallel work when more than one domain is
  involved, and one sentence saying what happens next.
- **Honest progress.** A percentage appears only when the gateway reports a
  measured total (for example 3 of 8 checks). There is never an ETA.
- **Illustrative until real.** While nothing has been produced, the overlay
  shows a small animated drawing for the domain (a sketch tracing a
  silhouette, a profile extruding, traces joining pads, signal packets, a mesh
  with contours, print layers, linked nodes), labelled *Illustrative
  activity*. When the operation reports a real preview, the drawing is
  replaced by that preview, labelled with what it is (*Preview*, *Exact
  result* or *Validated result*).
- **Actions from the operation only.** Review, cancel, retry and open buttons
  appear only when the operation lists them, which the gateway does only when
  it can carry them out. *Cancel* and *Retry* call the gateway's operation
  routes; a refusal (for example a run that can no longer be retried) is shown
  under the buttons. A retry starts a new operation that appears like any
  other. In Create, a review opens the **Decisions** panel; in Engineering it
  opens the existing approval card in place. There is no second approval
  screen.
- **A late result never wins.** When newer work replaces older work on the
  same item, the older operation shows as superseded; a result that arrives
  after the design moved on shows as stale and is not applied, and the newer
  revision stays current.
- **Connection.** If the live connection drops, the card says *Connection
  interrupted, reconnecting* and the drawing stops moving, so nothing implies
  progress that has not been reported. On reconnect the dashboard refetches
  the gateway's snapshot and carries on from it.
- **Motion and access.** The drawing is still under *prefers-reduced-motion*,
  can be paused, and pauses itself offscreen or in a hidden tab. Status is
  always written out as text, and changes are announced through a polite,
  throttled live region rather than on every update.
- **Inspect what it shows.** Expanded, the drawing is a button: it opens the
  newest artifact the operation produced (a twin work product opens in the
  twin viewer), or the run, and when nothing has been produced yet it opens
  the current step's detail (status, what it produced, what happens next).
  Each step named under *Working on* opens its own detail the same way. All of
  it works from the keyboard.

The operations live in a store shared with the Engineering view
(`store/operations-store.ts`), so switching views keeps the same operation,
stage, revision and preview. The overlay reads the project's operation
snapshot and stream (`/v1/projects/{project_id}/operations` and
`.../operations/stream`, described in the operations page of these docs).
While the project has operations, the journey rail's *working* state is
taken from them; before any exist it falls back to the local instruction
queue.

## Sketching on the stage

The product stage carries a freehand sketch layer. It is part of the stage,
not a separate workspace: drawing over the current concept or model, or over
the empty stage before anything exists, needs no setup and never asks for a
plane first.

| Tool | Does |
|---|---|
| Select (V) | The default. Clicks pass through to the stage (orbit the model, pick a result); clicking a stroke selects it |
| Draw (P) | A freehand pen stroke |
| Erase (E) | Removes every stroke the eraser passes over, as one step |
| Undo, Redo | Ctrl+Z and Ctrl+Shift+Z (or Ctrl+Y) |

Line, arc, circle, rectangle, smooth curve and region select appear only while
drawing is active, so the default toolbar stays at four tools. An arc runs from
where the drag starts to where it ends, through the drag's farthest point; a
smooth curve is a spline through a thinned copy of the drag. Region select
selects the strokes inside a dragged rectangle, and Delete removes a
selection. Esc cancels a stroke being drawn, then clears the selection, then
returns to Select. None of the shortcuts act while typing in the composer.

**Reference.** The image button shows a chosen project image or render under
the strokes, with adjustable opacity. It is labelled *Reference ·
illustrative*: it is something to trace over, not a measurement.

**Responsive.** Strokes render in the browser as the pointer moves, with
pointer capture and the browser's coalesced pointer events; the stroke being
drawn is one SVG path updated in place, and nothing on that path waits for
the network. The target is a visible stroke within 50 ms of the input.

**What a sketch is.** A human sketch, not CAD. The layer says *Your sketch ·
not CAD · scale unknown · no plane yet*, and the saved record says the same:
`authority: human_sketch`, `scale: unknown` and `plane_state: UNASSIGNED`.
Nothing reads a measurement from it. Points are kept in sketch units (the
shorter side of the drawing area spans one unit, origin at its centre), so a
sketch looks the same on any screen size and is not stretched by a resize.
A plane is only chosen when the sketch needs one (below).

**Saving.** The sketch saves itself 1.5 seconds after the last stroke is
finished, and on *Save sketch*. Each save is the whole sketch and becomes the
next revision of the project's `sketch_strokes` item (`SKT-SKETCH@1`, `@2`,
...), with the revision it was drawn on as its parent, a generated SVG and
the strokes as JSON (see [the twin schema](twin_schema.md)). A save carries an
id made in the browser; retrying it, after a failure or a lost reply, returns
the revision it already made instead of adding another. Each save appears in
the activity overlay as *Saving sketch*, then *Sketch saved* with the
revision. A failed save says so, keeps the strokes and offers *Retry save*.
Saves are counted in `metaforge_sketch_save_total` by outcome (`saved`,
`duplicate`, `failed`), and the `SketchSavesFailing` alert fires when more
than a fifth of them fail.

Saving is separate from the conversation: a sketch is never sent through the
instruction queue. Until it is saved, the sketch is kept as a draft in the
browser (per project), so a reload does not lose it; a browser with no draft
picks up the project's saved sketch. The undo history is not kept across a
reload.

**In Engineering.** A saved sketch is a work product of the project, so the
Engineering view and the twin viewer list it and preview it read-only, with
the same *Human sketch · not CAD · scale unknown · plane unassigned* label. It
is never chosen as the result shown on the Create stage; the sketch layer is
where it is drawn and shown. Once it is placed on a plane, the twin viewer
draws it on that plane, with its plane controls (see
[the dashboard tour](dashboard-tour.md)).

### Placing a sketch on a plane

Drawing never needs a plane. Once a sketch is saved, the status bar offers
**Use in 3D**. Only then does a compact choice appear beside it, without
covering the stage:

| Choice | Does |
|---|---|
| Front | Places the sketch on the XZ plane (seen from the front) |
| Side | Places it on the YZ plane (seen from the side) |
| Top | Places it on the XY plane (seen from above) |
| Select surface | Places the sketch on a flat surface of a part (see below). Enabled only when the project has validated exact geometry; otherwise disabled, and the choice says why |
| Keep as concept | Closes the choice. The sketch stays without a plane and nothing is written |

When the choice opens, MetaForge proposes a plane: another sketch of the
same component already on a plane, the orthographic view the camera is on
(in Engineering), the component's base plane, then a guess from the
sketch's name and shape. A clear proposal is outlined; when the guesses are
weak or disagree, the choice says *Not sure which plane fits. Please choose
one.* Nothing is placed until the person picks: the pick is applied exactly
as made, and a later proposal never overrides it.

Choosing a plane writes the next revision of the sketch's `sketch_plane`
item (`PLN-SKETCH@1`, ...), linked to the sketch revision it was chosen for,
and appears in the activity overlay as *Placing sketch on Front*, then
*Sketch placed on Front (XZ)*. The status bar then reads *on the Front
plane* instead of *no plane yet*. The sketch's scale stays unknown: placing
it says where it sits, not how big it is. See
[sketch planes](twin_schema.md#sketch-planes).

**Select surface.** When a part in the project has validated exact
geometry, *Select surface* opens a small picker: the part's model, with its
flat surfaces lit up under the pointer, and the same surfaces listed beside
it (named by the feature that made them, such as *top of PSK-BRACKET*, or by
the way they face). A click places the sketch on that surface: the plane's
origin is the surface's centre, its U axis runs along the surface's longest
straight edge, and its normal points out of the part. The plane item's next
revision is `ATTACHED`, and the surface's geometry signature is kept so the
surface can be found again. With several parts, a list chooses the part.
Without validated exact geometry the button stays disabled and the line
under the choice says why.

**When the surface changes.** When the part gets a new revision, MetaForge
looks for the surface again by its signature, never by a face number. If it
finds exactly one match, the sketch moves with it and nothing is asked. If
two surfaces could be it, or it is gone, the sketch keeps its last place and
Create says, in plain words, *The surface this sketch was on changed; choose
where it goes*, with **Pick the surface** (the surfaces that could be it are
marked), **Front**, **Side**, **Top**, **Keep as concept** and **Decide
later**. Until a choice is made, making the sketch editable for real and
building it as exact CAD wait (they are refused with the reason); drawing,
proposals and previews do not. The activity overlay shows the same question
as a card waiting for the person, with a link to the sketch in Engineering.
See [face attachment](twin_schema.md#face-attachment).

### Making a sketch editable

A saved sketch offers **Make editable**. It turns the strokes into shapes a
person can size and constrain, in the browser, in a few tens of
milliseconds, without leaving the stage:

- **Shapes over the strokes.** Each stroke is fitted with lines, arcs,
  circles, ellipses or, when nothing simpler fits, a smooth curve. A shape
  drawn solid is a confident fit; a dashed one is uncertain. Every shape and
  size is marked *estimated*: it is read off a drawing, not measured.
- **Rules as badges.** Small badges on the shapes propose rules: ends that
  meet, horizontal and vertical, parallel and square, tangent, same size,
  same centre and mirror symmetry. Clicking a badge accepts it (it turns
  green); clicking again takes it back. **Accept the likely ones** accepts
  every confident rule at once, leaving out any rule the others already
  imply. The panel lists the rest, each with accept and reject.
- **Sizes.** Every line's length and every circle's and arc's radius is
  listed with an *estimated* tag. Typing a value confirms it (*confirmed*)
  and re-solves the sketch from where it is; the strokes are never read
  again. The first size typed sets the scale of the whole sketch, so its
  proportions are kept. The unit is unknown until the sketch is placed on a
  plane with a unit; then it is that plane's unit.
- **What is left, in plain words.** *2 more dimensions or rules would fix
  the shape and size*, or *The shape and size are fully fixed*. When accepted
  rules cannot all hold, the panel names them (*These two rules conflict:
  Length of Line 2 = 60 and Length of Line 4 = 50*) and offers the fixes:
  remove one, or change a size to the value the others imply. Conflicting
  shapes are never moved.
- **Undo, compare, restore.** Undo and redo walk back through accepts,
  rejects and typed sizes. Compare draws the first fit beside the current
  shapes. Restore goes back to the version last saved.
- **More detail.** The detail toggle shows what the Engineering view
  shows: the degrees of freedom, every rule with its status, source and
  residual, and each shape's confidence and fit residual.

Making a sketch editable stores the proposal as the next revision of the
sketch's `sketch_interpretation` item (`SKI-SKETCH@1`, ...), which depends on
the sketch revision it was made from, and shows in the activity overlay as
*Editable sketch proposed*. **Make it a parametric sketch** stores the
current state and promotes it: the gateway re-checks every accepted rule on
the shapes and refuses if one does not hold, if nothing is accepted, or if
the sketch was redrawn since. The result is a `parametric_sketch` item
(`PSK-SKETCH@1`) holding only the accepted rules, and it waits for sign-off:
the panel shows *Waiting for your sign-off* with **Approve**, and the same
approval is in the Approvals list. Its feature plan lists candidate extrude
and revolve features with depth, angle and material marked as needed input:
nothing the drawing does not say is invented. See
[the sketch compiler](twin_schema.md#sketch-compiler).

### Generating a concept

**Generate**, at the top right of the stage, draws a freeform concept
sketch with an image model. It opens a small panel on the stage; there is no
separate studio. What the image is drawn from follows from what is there:

| There is | Mode | What is sent |
|---|---|---|
| Only a description | From your description | The description, any constraints and a style |
| Active references | From your references | Each reference for its role (below) |
| A saved sketch | From your sketch | The strokes, drawn to an image, refined or completed |

The panel asks for a description (the composer's draft fills it), a style
(industrial sketch, marker render, line art or concept drawing), how many
alternatives (one unless more are chosen: each is a paid image) and what to
avoid. The person's strokes are never changed: the generated image is a
separate layer under them.

**A region.** Drawing a rectangle with the region tool selects the strokes in
it and also becomes the region a generation may change. With *Change only the
selected region* ticked, the description becomes the instruction for that
region. The region is in the stage's sketch space: a generated image has its
centre at the sketch origin and its shorter side spanning one sketch unit,
so the region lands on the same pixels. A provider that takes an edit mask
gets one; with any other, the new image is put back outside the region.
Either way, the pixels outside the region, and inside any locked feature,
are compared with what was there. When less than 90% of them are kept
(`METAFORGE_SKETCH_PRESERVATION_THRESHOLD`), the proposal is marked *Check
it* with what changed, and the activity overlay asks for a review rather
than presenting it as done. Features to keep and things to avoid also go
into the prompt.

**Alternatives.** Every image is a proposal in the strip under the panel,
labelled *Generated concept · illustrative*. Each has **Accept**,
**Reject**, **Compare** (pick two to see them side by side with the share of
pixels that differ) and **Branch** (start a separate line of concepts from
it). Nothing replaces the accepted concept until the person accepts one; the
previous accepted concept stays in the line's history.

**While it runs.** Clicks are debounced, so a burst of them sends one
request. A new request on the same target (the sketch, or the sketch and the
same region) replaces one still running, and an identical one joins it. The
accepted concept stays on the stage during generation and when it fails. The
activity overlay shows the real stages (queued, started, image received,
completed) and no invented progress: no provider configured today streams
partial images. A result that arrives after another concept was accepted is
kept as history, marked stale, and cannot be accepted. The time to the first
stored proposal is measured and recorded on it; nothing promises a fixed
time.

**Spend.** Past the project's hourly image limit
(`METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR`, 20 by default), the panel says how
many images were made in the last hour and generates only when the person
chooses **Generate anyway**. An agent uses `twin.generate_sketch`, which is
held for approval like every paid image tool.

A generated image is illustrative: never an editable sketch and never CAD.
**Make editable** works on the person's own strokes. Turning a generated
image into editable shapes is not built yet.

### References and their roles

Each added reference shows as a compact chip with its role and whether the
next instruction includes it. The roles are:

| Role | Used as |
|---|---|
| Inspiration | Mood, style and materials only; its shape is not copied |
| Shape | Its overall shape and proportions are followed |
| How it works | How the product is used or works |
| Current design | The current design, kept recognisable |
| Evidence | Verification evidence. Never sent to an image model |
| Specification | Authoritative: its explicit values are stated facts |
| Making rule | Authoritative: a manufacturing rule |

Clicking the eye leaves a reference out of (or puts it back into) the next
instruction and generation. The arrow opens the rest only when asked for:
features to keep unchanged, and **Find facts**. Facts are candidates with a
citation (the reference, the line or region, the file's hash) and an
uncertainty. A value read off an image is always *estimated*, never
measured; only an explicit value with a unit in a specification or making
rule is *stated*. When two authoritative references state different values
for the same thing (120 mm against 125 mm), the activity overlay asks the
person to choose, naming both sources.

Every change of role, inclusion or file is kept as a version of the
reference. A reference must belong to the project and be a file type the
uploads accept. Which references were sent to which external provider, and
when, is recorded on the reference. An instruction carries its active
references with their roles, the current sketch revision and the selected
region.

## Conversation and instructions

The composer has three intents:

| Intent | What is sent | What can happen |
|---|---|---|
| Ask | no tools | Discussion only; the agent may not claim it changed anything |
| Change | `twin.propose_change` | A proposal that waits in Decisions until a person approves it |
| Design | every harness tool | A design pass; writes are still held by the approval policy |

Instructions are sent one at a time. Anything sent while a pass is running is
shown as *Queued* and goes next. Each instruction sent is a chat turn
operation named after the instruction (`chat:<id>`), so the one being sent
reads *Sending…* until the gateway's acknowledgement for that operation
arrives on the project stream, then *Received · working on it*, with
MetaForge's thinking bubble under it (see
[generative UI](generative-ui.md#while-the-agent-works)) until the reply
arrives. A send that fails stays in the conversation with *Retry*, *Edit*
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
| `PUT /references/{work_product_id}` | Set a reference's role, associations, inclusion and features to keep; each change is a version |
| `POST /references/{work_product_id}/facts` | Extract candidate facts with citations; conflicts between authoritative references are raised for review |
| `GET /references/conflicts` | Authoritative references that disagree |

The sketch layer uses these twin routes:

| Method and path | Does |
|---|---|
| `POST /v1/twin/projects/{project_id}/sketches` | Save the sketch as the next revision (idempotent per `save_id`) |
| `GET /v1/twin/projects/{project_id}/sketches/current` | The current revision with its strokes (`404` when none is saved) |
| `GET /v1/twin/projects/{project_id}/sketches/{sketch_key}/plane` | The sketch's plane (`UNASSIGNED` when none was chosen) |
| `POST .../plane/propose` | A proposed plane with who proposed it, its confidence and whether it is ambiguous. Writes nothing |
| `POST .../plane/confirm` | Place the sketch on the chosen plane (idempotent per `op_id`) |
| `POST .../plane/reassign/preview`, `POST .../plane/reassign` | Preview, then commit, a move to another plane in one of three modes |
| `POST .../plane/undo`, `POST .../plane/archive` | Undo the last plane change, or archive the plane, each as a new revision |
| `GET /v1/twin/projects/{project_id}/attachable-geometry` | The parts whose head is validated exact geometry, with the reason when there are none |
| `GET /v1/twin/projects/{project_id}/items/{item_key}/faces` | A validated revision's flat surfaces, with the GLB triangle map the picker highlights |
| `POST .../plane/attach-face`, `POST .../plane/resolve-reattachment` | Place the sketch on a surface, or answer *choose where it goes* (pick a surface or detach), idempotent per `op_id` |
| `POST /v1/twin/projects/{project_id}/sketches/{sketch_key}/interpretations` | Store a proposal made by **Make editable** (idempotent per `interpretation_id`; `409` when the sketch was redrawn since) |
| `POST /v1/twin/projects/{project_id}/interpretations/{key}/promote` | Promote a proposal's accepted rules to a parametric sketch held for sign-off (idempotent per `promote_id`; `409` when a rule does not hold) |
| `POST /v1/twin/nodes/{node_id}/approve-parametric-sketch` | Sign off a parametric sketch |
| `POST /v1/twin/projects/{project_id}/sketch-generations` | Start a concept generation as a job on its target (`409` with `spend_confirmation_required` past the hourly limit; refused for an agent) |
| `GET /v1/twin/projects/{project_id}/sketch-generations` | The accepted concept lines with their history, and recent proposals |
| `GET .../sketch-generations/compare?a=&b=` | Two concepts side by side, with the share of pixels that differ |
| `POST .../sketch-generations/{node_id}/accept` | Make a proposal the accepted concept (`409` when it is stale or another was accepted since) |
| `POST .../sketch-generations/{node_id}/reject`, `POST .../branch` | Set a proposal aside, or start a separate line from it |

"A person only" means a caller sending `X-MetaForge-Surface: agent` is
refused with `403`. The full schemas are in the
[gateway API reference](reference/gateway-api.md).

## Not yet

- An editable sketch's shapes are lines, arcs, circles, ellipses and smooth
  curves; there are no angle or point-to-point distance dimensions yet, and
  shapes cannot be dragged. A generated (raster) image cannot be made
  editable yet: only strokes drawn on the stage.
- Generated concepts: no configured provider streams partial images, so
  there is no progressive preview. Front, side and top views are not
  proposed together. Facts are read from text references and images, not
  from PDFs.
- Planes are Front, Side and Top, or a flat surface of a validated part;
  curved surfaces, and offset or custom planes, are later steps.
- Pen pressure is recorded with each point but not yet drawn as varying
  width.

- Readiness is read by polling every five seconds, not pushed.
- Marking a dimension directly on a photo or the model, and calibrated camera
  measurement, are not built; measurements are typed, with their source.
- Comparing two revisions side by side is in the Engineering view's revision
  tools, not on the Create stage.
- Finding manufacturers, quotes, orders and payment need a connector; the Make
  tab says so rather than imitating one.
