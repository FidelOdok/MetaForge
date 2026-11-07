# The Create view

Create is the dashboard's nontechnical view of a project. Anyone can describe a
product in their own words, watch it take shape, choose how to make it, check
what comes out and improve it, without opening the engineering tools:

**Imagine → Develop → Check → Make**

**Imagine** captures what the product is for and what it looks like: the
studio the assistant builds for the product, and the sketch tools. Checks in
Imagine are screening only. Once Imagine is handed over, the MetaForge twin
drives **Develop**, **Check** and **Make** (exact CAD, electronics, firmware,
simulation, manufacturing checks, contracts and release), and those three
stages show the twin's state; they are not a second editor.

It is a view, not a separate product. A creation *is* a project: its
conversation is the project's chat thread, its designs are the project's work
products in the digital twin, and its decisions are the project's approvals.
The **Create / Engineering** switch in the topbar moves between the two views
of the same records, so nothing is copied and nothing is lost on the way.

## Where it lives

| | |
|---|---|
| Dashboard | `/create` (start or reopen a creation), `/create/:projectId` (one creation) |
| Gateway | `/v1/create/{project_id}/...` (`api_gateway/create/`), `/v1/twin/projects/{project_id}/imagine/...` (the Imagine studio and handover) |
| Records | SQLite at `~/.metaforge/create.db` (`METAFORGE_CREATE_STORE_PATH`) |
| Reused | `/v1/projects`, `/v1/chat`, `/v1/twin`, `/v1/approvals`, `/v1/uploads`, `/v1/library/machines`, `/v1/manufacture/release` |

The gateway stores only what the Create journey adds on top of the project:
the **brief**, the **manufacturing route**, the **inspection plan**, the
**builds** (manufacturing attempts with their evidence, results and
acceptance) and a **history** of every change.

## The screen

Create is deliberately calm: a header, the stage rail, and one column of
content for the stage on screen, with the conversation beside it.

- **Header.** The creation's name, and one compact status spot holding
  **Decisions** (with the count of waiting approvals) and the activity card.
  Nothing else sits in the header.
- **Stage rail.** The four stages, **Imagine**, **Develop**, **Check** and
  **Make**, are the navigation of Create: each stage owns its content. Each
  tab also says where the creation stands (done, current or to do) with a
  one-line summary (*Concept captured*, *Dimensioned model*, *2 to resolve*,
  *Making finished*). Any stage can be opened; work in a later stage can
  already be under way. A creation opens on the stage where it stands. The
  stage on screen is in the address (`?stage=check`; Imagine's tab is
  `?imagine=sketch`), so back, forward and direct links work.
- **Conversation** (beside the stage). The project's chat thread. Each
  message says what it is: a *Question* and its *Discussion*, a *Change
  request* and its *Proposed change · awaiting your decision*, or a *Design
  pass* and what it *executed with tools*. The composer grows with the draft
  (no height cap, no inner scrollbar), shrinks as text is removed,
  re-measures when its width changes, and takes pasted or dropped files.
  Changes to the design go through here (or through Imagine rework), never
  through an editor on the Develop, Check or Make stages.

At phone width (375 px and up) everything is one column: the rail, the
stage, then the conversation.

### Collapsible conversation

The conversation has a collapse button. Collapsed, it becomes a slim rail
showing how many instructions are waiting. The choice is the person's own
preference, kept in this browser (`metaforge.create.panels` in local
storage) and the same in every creation; with storage unavailable it simply
starts expanded.

### One primary action per stage

Each stage offers exactly one primary action; everything else is in a single
**More** menu.

| Stage | Primary action | In More |
|---|---|---|
| Imagine (no studio yet) | **Ask the assistant** (puts *Start the Imagine studio for this product.* in the composer) | |
| Imagine (draft) | **Hand over to engineering** | |
| Imagine (handed over) | none: the studio is read only; **Reopen Imagine** is a secondary button | |
| Develop | **Make it editable** (an accepted concept whose features are not accepted yet), **Build exact model** (a parametric sketch exists), **Ask for the exact model** (puts a design request in the composer) or, once a dimensioned model exists, **Describe a change** | *Make it editable*, *Show as simple 3D parts*, *Discuss this model*, each where it applies |
| Check | **Run the checks** (the interface contracts and the manufacturing checks) | *Ask the assistant about these results* |
| Make | **Plan the making**, or **Inspect the result** once the latest build has finished | the other one, *Ask the assistant to prepare the release* |

### Waiting approvals

Inside a creation, waiting approvals are a count badge on **Decisions** in
the header; selecting it opens the decisions in a small panel under it. The
page-wide *approvals waiting* notice that other pages show is not shown over
a creation, so it never covers the stage. The approval flow itself is
unchanged.

## Activity overlay

Work that takes time (a design-flow run, a tool job, a Create chat turn) is
shown as an **operation** in a small activity card. The card sits in the
header's status spot, beside **Decisions**, so it never covers the stage,
the sketch tools, the drawer, the composer or the conversation. It stays the
compact card until expanded. It can be expanded into a larger, slightly
translucent overlay, and minimised again with its button or **Escape**.
Minimising or closing it never cancels anything. The card is in the header,
so the keyboard reaches its controls before the rail and the stage.

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
  other. In Create, a review opens **Decisions** in the header; in Engineering it
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
While the project has operations, the journey's *working* state is
taken from them; before any exist it falls back to the local instruction
queue.

## Imagine

Imagine holds intent and concept capture. Its content is one of two tabs,
**Studio** and **Sketch**, with the handover under it.

### The studio

The studio is a page the assistant builds for this product: what it is for,
the scenarios it must survive, the options and the chosen parts, the
assumptions, and early screening. It is the formgraph studio, merged into
MetaForge and styled like it. When there is no studio yet, Imagine shows one
calm empty state: *Ask the assistant to start the studio for this product*,
with **Ask the assistant**, which puts the request in the composer.

Create hosts the studio's engine page (`engine_url` from
`GET .../imagine/studio`) in a frame sandboxed to scripts only, with no
same-origin access, so the page can reach neither the dashboard nor its
storage. The two talk only by messages of the form `{fg: 1, type, ...}`:

| From | Message | Create does |
|---|---|---|
| Page | `fg:ready` | Answers with `fg:snapshot {collections}`, the twin-backed state of each collection, and with `fg:readonly {value: true}` when the studio has been handed over |
| Page | `fg:write {collection, id, data}` | Sends it to `POST .../imagine/events` as `{event_id, kind, data}` with a fresh `event_id`, the `kind` named for the collection (`choices` is `choice`, `scenarios` is `scenario`, `contextEdits` is `context_edit`, `assumptions` is `assumption_confirmed`, `requirements` is `requirement`, `ledger` and `screening` as they are). Not sent once the studio is read only |
| Page | `fg:height {px}` | Resizes the frame (between 240 and 6000 px) |
| Page | `fg:select {component_id}` | Selects that component in the shared component selection, so the 3D view highlights it wherever it is shown |
| Page | `fg:download {name, mime, base64}` | Hands the file to the person as a download (the name stripped of any path); it is never opened or run |
| Create | `fg:readonly {value}` | Sent when the studio is handed over, or reopened, while the page is up |

A message is accepted only from the frame's own window, with the opaque
origin a sandboxed frame has, and only in one of these shapes; anything else
is ignored. Nothing the page sends is ever executed. A write that could not
be saved says so under the frame.

### The handover

Under the studio, **Ready for engineering?** lists the handover checklist
from `GET .../imagine/handover`: the ledger, the assumptions (confirmed or
explicitly accepted), the scenario tests, the requirements with their source
and verification method, one chosen part per function, the manufacturing
context, the concept assembly with component ids and an illustrative concept
mesh, the proposed interface contracts and the screening results. Each item
is *Done*, *Missing* or *Assumed*.

**Hand over to engineering** calls `POST .../imagine/handover` with an
`op_id`. When something is missing the gateway refuses with `409` and Create
lists exactly the missing items. Once handed over, the studio's revision is
frozen read only, the twin is seeded from it, and Imagine reads *Handed over
to engineering · revision 3 · read only*. **Reopen Imagine** asks why, and
only after the person confirms calls `POST .../imagine/rework` with the
reason: Imagine opens on a new studio revision, and the Develop and Check
evidence that depends on it is marked stale.

### Sketch

The **Sketch** tab shows the drawing paper on its own. There is no 3D view
under it and no trace of one, so the paper and the model never look merged.
**Generate a concept** and **References** sit above the paper.

The editable sketch, *Generate a concept*, the sketch plane choice and the
references open in one drawer beside the drawing, never over it. Only one is
open at a time: opening another replaces it. The drawer can be collapsed to
a slim tab and opened again, or closed.

### Sketching

The Sketch tab carries the freehand sketch layer. Drawing on the paper needs
no setup and never asks for a plane first.

| Tool | Does |
|---|---|
| Select (V) | The default. Clicking a stroke selects it |
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

**Contrast.** Strokes, the fitted shapes of an editable sketch and its rule
badges take their colours from sketch tokens that follow the theme: dark ink
on the light paper, light ink on the dark one. Each keeps at least 3:1
contrast against the paper in both themes. The reference image stays faint
on purpose.

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
is never chosen as the model shown in Develop; Imagine's Sketch tab is where
it is drawn and shown. Once it is placed on a plane, the twin viewer
draws it on that plane, with its plane controls (see
[the dashboard tour](dashboard-tour.md)).

#### Placing a sketch on a plane

Drawing never needs a plane. Once a sketch is saved, the status bar offers
**Use in 3D**. Only then does the choice open, in the drawer beside the
drawing:

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
two surfaces could be it, or it is gone, the sketch keeps its last place, the
status bar offers *Surface changed · choose*, and the drawer says, in plain
words, *The surface this sketch was on changed; choose
where it goes*, with **Pick the surface** (the surfaces that could be it are
marked), **Front**, **Side**, **Top**, **Keep as concept** and **Decide
later**. Until a choice is made, making the sketch editable for real and
building it as exact CAD wait (they are refused with the reason); drawing,
proposals and previews do not. The activity overlay shows the same question
as a card waiting for the person, with a link to the sketch in Engineering.
See [face attachment](twin_schema.md#face-attachment).

#### Making a sketch editable

**Make editable**, under a saved sketch in the Sketch tab, turns the strokes
into shapes a person can size and constrain, in the browser, in a few tens
of milliseconds, without leaving the paper. The
shapes are drawn over the strokes; the panel with the rules and sizes is in
the drawer:

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

**Generate a concept** (above the paper in Imagine's Sketch tab) draws a
freeform concept sketch with an image model. It opens in the drawer. What the
image is drawn from follows from what is there:

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
region. The region is in the paper's sketch space: a generated image has its
centre at the sketch origin and its shorter side spanning one sketch unit,
so the region lands on the same pixels. A provider that takes an edit mask
gets one; with any other, the new image is put back outside the region.
Either way, the pixels outside the region, and inside any locked feature,
are compared with what was there. When less than 90% of them are kept
(`METAFORGE_SKETCH_PRESERVATION_THRESHOLD`), the proposal is marked *Check
it* with what changed, and the activity overlay asks for a review rather
than presenting it as done. Features to keep and things to avoid also go
into the prompt.

**Alternatives.** Every image is a proposal in the strip under the form,
labelled *Generated concept · illustrative*, each card with its
manufacturing note (*Manufacturing not assessed* until one is assessed). Each has **Accept**,
**Reject**, **Compare** (pick two to see them side by side with the share of
pixels that differ) and **Branch** (start a separate line of concepts from
it). Nothing replaces the accepted concept until the person accepts one; the
previous accepted concept stays in the line's history.

**While it runs.** Clicks are debounced, so a burst of them sends one
request. A new request on the same target (the sketch, or the sketch and the
same region) replaces one still running, and an identical one joins it. The
accepted concept stays during generation and when it fails. The
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

**Manufacturing note.** Every concept carries a short, truthful note about
how it might be made, from the project's
[manufacturing context](twin_schema.md#manufacturing-context):
*Manufacturing not assessed* when nothing informed it, or *Informed by: FDM
(assumed), PLA (assumed) · Manufacturing not assessed* when a stated,
decided or assumed process and material did. A first-time user never has to
choose a process first: with none stated, MetaForge proposes candidates as
assumptions. A concept asked for as *speculative* is labelled *Speculative
concept* and the context is recorded but not applied. No concept is ever
labelled verified or production-ready: no manufacturing check has run on an
illustration.

### References and their roles

References are listed in Imagine's Sketch tab (**References** opens them in
the drawer) and attached through the composer. Each added reference shows as a compact chip with its role and whether the
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

## Develop

Develop shows the twin's current model and what needs attention, with one
primary action (see [the table above](#one-primary-action-per-stage)).

### The model and its label

The model is the existing 3D viewer, read only, with one label above it: the
maturity word, **Illustrative**, **Preview**, **Exact** or **Validated**,
then the status line, the representation level and what it was made from,
and the item and revision:

| Shown | Status line |
|---|---|
| An image or generated concept | *Concept · illustrative* |
| An AI 3D concept | *3D Concept · illustrative* |
| Simple 3D parts (L1) | *3D Preview · simple parts, sizes estimated* |
| A provisional mesh or preview geometry | *3D Preview · provisional* |
| Exact CAD | *Engineering Geometry · exact model available* |
| Exact CAD whose checks are running | *Engineering Update · validation pending* |
| Validated CAD | *Engineering Geometry · validated* |
| Exact CAD whose checks failed | *Preview available · Engineering update needs attention* (the last checked version is kept) |

A provisional mesh is never called exact, and an illustrative or provisional
item never carries *verified*, *validated* or *production-ready* wording: the
label passes through a guard that falls back to the plain illustrative
wording. The guard also reads the artifact's
[representation level](twin_schema.md#representation-levels): an AI 3D
concept (L0) or simple parts (L1) is never shown as Exact or Validated,
whatever its record says. The level reads, for example, *L1 · simple parts,
sizes estimated · from SKT-LAMP@2* or *L3 · from the validated exact model ·
from CAD-BASE@4*. Manufacturing results are not repeated on the label: they
are in [Check](#check).

**Which model.** Develop shows the best real representation of each item's
current revision: validated exact geometry (L3) first, then an exact model
not yet validated (L2), simple 3D parts (L1), an AI 3D concept (L0), then a
picture; the newest of a level. An older revision of an item is never
preferred over its current one, and an out of date revision ranks last. The
Engineering 3D model view, opened with nothing selected, picks the same one.

**What is running.** While a pass or the engineering checks run, a line on
the model says so (*Preview updated · Engineering checks running*, or
*Working on the next version. This is the latest finished one.*) and the
last finished model stays on screen. The activity card in the header has the
detail.

**Needs attention.** Under the model, Develop lists the creation's
[readiness](#readiness) blockers, each with what clears it.

**Selecting a part.** Clicking a part of a 3D model selects its component
(the `component_id` in the mesh's glTF extras, or the model's only
component). This works part by part on a multi-part CAD model too: the exact
model's STEP names each part's component, and the converter writes it into
that part's extras. The selection is shared: a part picked in Engineering,
in the studio (`fg:select`) or in Check's manufacturing results is selected
here too.

### The editable part: preview, exact model and handles

Once a parametric sketch exists, Develop shows it as a part. Nothing needs
to be selected: the newest parametric sketch leads (and selecting its CAD
item shows the same view). In Create the part is shown read only: the
handles and the number fields below are in Engineering's inspector.

- **Preview.** The part is drawn in the browser from the same definition
  the exact model is built from, so a change shows at once: *3D Preview ·
  provisional*. Typing a new depth, or dragging a handle, redraws it
  without asking the gateway anything. When the sketch does not say how
  deep the part is, or its scale or plane is not confirmed yet, the preview
  still draws it and says what it assumed, for example *Depth not set ·
  showing an assumed 4 mm* or *Scale not confirmed · drawn at an assumed 1
  sketch unit = 1 mm*. An assumed value is never used to build the exact
  model.
- **Handles.** An orange handle on the top face drags the depth along the
  part's normal; a handle on the rim of a circle with a confirmed radius
  drags that radius. A teal handle in the middle of a line with a confirmed
  length drags that length along the line: every move solves the sketch's
  accepted rules again in the browser, so the lines joined to it follow and
  square corners stay square. On a revolved part a violet arc around the
  axis shows how far it turns, and its handle drags the angle (whole
  degrees, up to a full turn). The preview follows the pointer, and the
  change is saved once, when the pointer is released: the parametric
  sketch's next revision (pinned to the revision that was on screen), then
  one exact build. Escape during a drag puts the value back. The same
  values have number fields under the view (the keyboard way): Enter or
  **Apply** saves. A part with no material yet asks for one there. The
  dragged mesh is never what is saved: only the parameter (and, for a
  length, the solved positions of the sketch's lines), through the normal
  parametric path, and it is checked against the sketch's accepted rules
  first.
- **A length the rules cannot hold.** When a dragged length cannot hold
  with the other accepted rules (two confirmed lengths on opposite sides of
  a rectangle, say), the preview stays at the last length that worked and
  says why in plain words, for example *A length of 50 mm cannot hold with
  the other accepted rules. These two rules conflict: ... Remove one, or
  change a dimension. Nothing was changed.* Releasing the handle then saves
  nothing.
- **Swap.** While the exact model is built the view keeps the preview and
  says *Engineering Update · validation pending*. When the validated exact
  model built from this very definition arrives, the view swaps to it:
  *Engineering Geometry · exact model available*. The view learns this from
  the live activity stream: when the build reports the exact geometry built,
  its verdict or its failure, the view reads the part again and swaps, or
  shows the failure, at once (a build of the same part started elsewhere
  counts too). Only while the stream is offline does the view look again
  every 1.5 seconds instead. If the comparison found
  that the exact model differs from what the preview showed, a short note
  says where, for example *The exact model differs from the preview in
  volume, holes.*
- **When the exact model fails.** The preview stays, the last valid exact
  model stays faintly underneath it, and the view says *Preview available ·
  Engineering update needs attention* with **Retry** (the same retry as the
  activity overlay). Nothing is called exact.

In Engineering, the inspector of a parametric sketch, or of the CAD item it
builds, shows the same view with the comparison values (bounding box, main
dimensions, volume, sampled surface distance and holes, each with its
tolerance), the revisions involved and how long the last preview rebuild
took. See [L2 preview, comparison and parameter
commits](twin_schema.md#l2-preview).

### Build exact model

**Build exact model** (Develop's primary action once a parametric sketch
exists) starts the exact build of the newest parametric sketch. It first reads the sketch as it is
now:

- When every input only a person can give is known (the depth of an
  extrude or the angle of a revolve, and the material), it starts the build
  at once through the exact build route, and the activity overlay opens to
  show its progress. A sketch whose exact model is already built from the
  same definition is not built again; the stage says so.
- When an input is missing, the stage asks one short question for it, with
  concrete choices: for a depth, fractions of the profile's own size (for
  example *A quarter of its width (10 mm)*); for an angle, a full, half or
  quarter turn; for a material, only what the manufacturing context names
  or lists as a candidate. A field takes any other value, and **Not now**
  leaves it. Nothing is filled in on the person's behalf. Once every input
  is answered, the answers are saved as the sketch's next revision through
  the parameters route, which starts the one exact build.
- When something has to happen first, Develop says what and offers the way
  there: an unapproved sketch opens Imagine's Sketch tab with the editable
  sketch in the drawer, and a sketch with no confirmed plane or unit in
  millimetres opens the plane choice there. No build is started.

Approvals and refusals are the routes' own; a refusal is shown as the
route gave it. Without a parametric sketch, **Ask for the exact model** puts
a design request in the composer, ready to send.

### Refining the model automatically

The CAD design loop ([how it works](architecture/design-flow-harness.md#cad-design-loop))
writes a part's CadQuery, builds and checks it, renders it, has a vision
model review it and revises it a few times at most. It is no longer started
from Create: ask the assistant, or use Engineering's inspector, which shows
each run's iteration timeline (the rendered views, the review with its
issues, the measurements and checks, the DFM findings, the models and their
cost, and the script changes from one iteration to the next) and its
**Use this version** or **Keep the previous one**.

### Making a 3D concept

An illustrative 3D concept (correspondence level L0: approximate visual
form, no CAD meaning) is made by `twin.generate_concept_mesh` as a
background job, from the accepted generated concept, else the newest concept
image, else the brief in words. Create has no button for it: ask the
assistant (the tool is paid and held for approval, under the project's
hourly mesh cap, `METAFORGE_AUTO_APPROVE_MESH_PER_HOUR`, 3 by default). The
result is a GLB `render` labelled *AI 3D reference model, illustrative only*,
shown in Develop as **Illustrative** · *3D Concept · illustrative*. The model
records its source, who asked, from which surface, the project's policy mode
and whether that policy alone would have approved it.

Splitting the concept into parts, matching the parts to components, and
redoing or sealing one part are in Engineering's inspector (concept parts),
with the split's provenance and the part mapping. Each result is a new
version to accept or keep aside, and the model stays illustrative.

### Showing it as simple 3D parts

**Show as simple 3D parts** (in Develop's **More**) draws the concept's block layout as
a 3D model with one named part per component (correspondence level L1: parts
identified, sizes estimated). It calls
`POST /v1/twin/projects/{id}/structured-mesh`, which is free and answers at
once, and the result is shown as **Preview** · *3D Preview · simple parts,
sizes estimated*. An unchanged layout shows the model already made rather
than a new one. With no concept sketch yet Develop says there is no layout
to build from. It is also drawn once when a concept assembly is
recorded. See [representation levels](twin_schema.md#representation-levels).

### Making a concept editable

Once a concept is accepted (its parts are recorded with it), Develop's
primary action becomes **Make it editable**. It opens a short panel under
the model that asks for a proposal if there is none yet, then shows how the
product would be built in plain words ("We'd build this as: the base: a
round disc; the shade: a round rod, hollowed out"). There is no feature tree
and no parameters here. The questions that need the person come next as
choices (hollow or solid, where an opening goes, how it will be made); the
accept button waits until the consequential ones are answered.

Accepting takes the features the concept itself supports (not the ones a
model only suggested) and turns each part into an editable sketch held for
sign-off in Approvals. Sizes that were only estimated from the concept are
asked for again before the exact model is built; nothing is assumed for
them. Engineering shows the same proposal in full under **Feature history
(proposed)** in the inspector: each feature, each parameter with its value,
estimated, confirmed or unknown status and source, the assumptions, the DFM
strategy note with the rules that shaped it, and what it was made from. See
[parametric reconstruction](twin_schema.md#parametric-reconstruction).

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

*Actions* beside the intents fills in a common request (where it stands,
design this, run a stress check, review the gate, what is waiting, did it
work) and switches to *Design*; nothing runs until it is sent (see
[actions](generative-ui.md#actions)). The model button next to it shows which
model answers, and lets the person choose their own (see
[choosing the model](generative-ui.md#choosing-the-model)).

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

### What a request changes

Before the agent acts on an instruction, the gateway classifies what it would
change, and Create says so in one plain line under the message:

| Request | Line | What happens |
|---|---|---|
| "Make it red" | *Colour change only. Your engineering model stays the same.* | The look is recorded in the [appearance record](twin_schema.md#appearance-record) and shown on the stage. CAD is not rebuilt, the manufacturing context is not touched and nothing is marked stale |
| "Make it metallic", "chrome", "a wood look" | The reply asks *Do you mean the look, or the actual material it is made from?* with **The look** and **The material** | Nothing changes until one is chosen; the answer carries the request, so it is not asked again |
| "Use aluminium instead of plastic" | *Material change to Aluminium...* with **Use Aluminium** | The person confirms; the material is stated on the manufacturing context, and the checks that depend on it are marked stale |
| "Make it wider", "move the opening" | *Shape change...* | The design change, parametric and exact paths; dimensions not given are proposed, never invented |
| "Add ventilation holes" | *New feature...* | An editable feature, then the relevant checks |
| "Make it more aerodynamic" | *Performance goal...* | Alternatives and how to evaluate them come first |
| "Make it futuristic" | *Style direction...* | Options are explored first |
| "What material is the lid?" | *A question...* | An answer; nothing changes |

While the reply is still coming, the line comes from the turn's `classified`
event on the project stream; afterwards from the classification the gateway
returns with the message. A request the rules do not recognise shows no line.
The part selected on the stage travels with the instruction, so "make it red"
colours that part rather than the whole product.

References (photos, sketches, documents, design files) upload the moment they
are attached, through `/v1/uploads` with the dashboard's own approval, and
each shows *Uploading*, *Added*, *Not supported* (with the reason) or
*Failed*. Added ones are listed in the brief's `references`.

## The brief

The brief is the creation's record of intent. It is no longer edited in a
Create panel: the Imagine studio captures intent, and the handover maps the
studio's ledger, requirements and assumptions into requirements and brief
items with their source and verification method. The first request in the
conversation still becomes the brief's *Intended use*, in the person's
words. The brief holds:

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

Each item is *Confirmed*, an *Assumption* or *Open*. **Measurements** carry
an explicit unit and say whether they are *User-confirmed*, *Estimated* or
*Unconfirmed*, and where they came from. A dimension marked *Critical* must
be user-confirmed before a build can be released. A camera picture alone is
never treated as a calibrated measurement. A write that lands after a change
made elsewhere is refused with `409` and the newer record.

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
manufacturable, safe for its use, or right for every process. Develop lists
the blockers under **Needs attention**, and the rail's summaries come from
the same state.

## Check

Check shows the twin's verification state, read only, with one primary
action, **Run the checks**, which evaluates the interface contracts and the
manufacturing checks again.

### Parts that must fit together

When the project has [interface contracts](twin_schema.md#interface-contracts)
(the [desk lamp golden path](examples/desk-lamp.md) seeds four), Check lists
each one in plain words with where it stands, for example *The circuit board
must fit inside the base: not checked yet*. The words are *checked, it
fits*, *does not fit yet*, *not checked yet* and *needs checking again after
a change*; a fit is only claimed after a real check on real data.
Engineering's **Interfaces** tab shows the same contracts with their coupled
parameters, agreements, status and the evidence of each check.

### Manufacturing checks

Check lists the open manufacturing (DFM) results by part, in plain words,
with the overall status and its disclaimer; a result that blocks making says
so. Selecting a part's name highlights it in the model (the shared component
selection). The checks are heuristics on the best geometry each part has
(see [DFM findings](twin_schema.md#dfm-findings)) until exact checks run on a
validated model. Deciding on a suggestion (keep the design, try the
suggested fix, explore another process) goes through the assistant or
Engineering's **DFM** tab, which also has the rule profile and version,
measured against limit, anchors, evidence, the verification items and the
generated DFM report.

## Decisions

**Decisions** in the header lists the project's pending approvals in plain words, each
labelled with the kind of commitment it is, so a design approval is never
mistaken for spending or moving a machine: *Design approval*, *Spending*,
*External file sharing*, *Physical machine action*, *Adding files* or *Process
checkpoint*. The buttons are exactly the decisions the gateway allows for that
item, a reason is asked for where the gateway requires one, and findings and
*Full evidence in Engineering* are a click away. The approval rules are the
gateway's own ([Approvals](approvals.md)); Create adds none.

Releasing a build and accepting it are Create's own decisions, in the Make
stage.

## Make

Make shows the release checklist and the builds, with one primary action:
**Plan the making**, or **Inspect the result** once the latest build has
finished. Each opens in place, one at a time.

**Release checklist.** Once a process with exact checks is chosen (today 3D
printing), Make lists the manufacturing release checklist of the current
revision (see [the release checklist](twin_schema.md#release-checklist)),
each item passed, failed or not applicable, under one line saying how far
the product is from being made. It reads **Ready for prototype** only when
every required part has a validated exact model, the exact manufacturing
analysis passed (or a person accepted its issues) and each part's STEP
passed its round trip, and **Ready for production** only when, in addition,
the physical verification is approved and the release is approved.
Otherwise it reads *Not ready to make yet: 3 next steps*. It is never a
percentage and never claims a readiness the checklist does not back.

### Making it

Three routes: **Make at home**, **Use my workshop** and **Have it made**.
**Plan the making** suggests a process from the brief and the model, says why (a metal
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

### Inspection and acceptance

The **inspection plan** is set before making. **Inspect the result** can turn the
success criteria into one check per line to start from. Each check records the
feature, the acceptance condition or tolerance, the evidence needed, the method
(camera observation, calibrated measurement, machine telemetry, operator
confirmation, test or instrument), who reviews it (the person, or an agent
allowed to evaluate it) and whether failing it blocks acceptance.

Evidence comes from the optional **camera** (pick a camera, live preview,
positioning guidance, resolution, snapshots), from uploaded photos, or from
measurements and notes. Nothing is recorded continuously: only the snapshots
taken are kept, as files in the project. A camera that is refused or missing
falls back to uploading photos. Closing the inspection ends the camera
session; it is started again on purpose.

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

In the offline sample workspace (`?demo=1`) the sample project is a complete
creation with a delivered build waiting for its last check, so the whole
loop can be shown without a gateway.

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
| `GET /v1/twin/projects/{project_id}/parametric-sketches/{sketch_key}/preview-definition` | The shared definition the part's preview is drawn from, with the exact model it swaps to and, given `job_id`, the build's state |
| `POST /v1/twin/projects/{project_id}/parametric-sketches/{sketch_key}/parameters` | Save a depth, angle, material or circle radius as the sketch's next revision and start one exact build (`409` when the sketch moved on since `base_revision`) |
| `POST /v1/twin/projects/{project_id}/sketch-generations` | Start a concept generation as a job on its target (`409` with `spend_confirmation_required` past the hourly limit; refused for an agent) |
| `GET /v1/twin/projects/{project_id}/sketch-generations` | The accepted concept lines with their history, and recent proposals |
| `GET .../sketch-generations/compare?a=&b=` | Two concepts side by side, with the share of pixels that differ |
| `POST .../sketch-generations/{node_id}/accept` | Make a proposal the accepted concept (`409` when it is stale or another was accepted since) |
| `POST .../sketch-generations/{node_id}/reject`, `POST .../branch` | Set a proposal aside, or start a separate line from it |
| `POST /v1/twin/projects/{project_id}/concept-mesh` | Make a 3D concept as a job (from the accepted concept, the newest concept image, or the brief); `409` with `spend_confirmation_required` when a person must confirm; refused for an agent |
| `GET /v1/twin/projects/{project_id}/concept-mesh` | The newest 3D concept, the job still running, the source the next one would use, and the hourly count |
| `POST /v1/twin/projects/{project_id}/classify-change` | What a request would change: class, confidence, target, handling and the plain line. Writes nothing |
| `GET /v1/twin/projects/{project_id}/appearance`, `PUT ...` | The product's and each component's look; a `PUT` sets one, and never touches CAD or the manufacturing context |
| `GET /v1/twin/projects/{project_id}/dfm` | Preliminary manufacturing guidance by part: the badge's suggestion count, each finding and its status |
| `POST .../dfm/evaluate`, `POST .../dfm/findings/{finding_id}/decision` | Run the checks now; **Keep design**, **Try suggested fix** or **Explore another process** on one suggestion |
| `GET /v1/twin/projects/{project_id}/dfm/checklist` | The manufacturing release checklist and the readiness the chip may show |
| `POST .../dfm/report`, `GET .../dfm/report` | Generate the revision's DFM report; read the latest (`?format=markdown` for the rendered copy) |
| `POST .../dfm/verification/{item_id}/evidence` | Record evidence for a physical verification item; it counts once a reviewer approves it |
| `POST /v1/twin/projects/{project_id}/reconstruction`, `GET ...` | Propose how an accepted concept would be built as editable features; read the latest proposal per part |
| `POST .../reconstruction/{key}/accept` | Accept a part's proposed features with answers; its editable sketch waits for sign-off |
| `POST /v1/twin/projects/{project_id}/cad-loops`, `GET ...` | Start the CAD design loop on a parametric sketch as a job (`409` with `spend_confirmation_required` past the hourly limit; refused for an agent); list the runs, newest first |
| `GET .../cad-loops/{node_id}`, `POST .../cad-loops/{node_id}/decision` | One run with every iteration; **Use this version** (`accept`, an exact build; `409` when the model moved on) or **Keep the previous one** (`reject`), a person only |

The Imagine stage uses these routes, under
`/v1/twin/projects/{project_id}/imagine`:

| Method and path | Does |
|---|---|
| `GET /studio` | The studio: `status` (`none`, `draft` or `handed_over`), `revision`, `config_ref`, `engine_url`, `config`, `updated_at` and the `handover` record |
| `PUT /studio` | A new studio revision from a product config |
| `POST /events` | One studio write, `{event_id, kind, data}`; idempotent per `event_id` |
| `GET /handover` | The handover checklist, `{ready, items: [{id, label, status, detail}]}` |
| `POST /handover` | Hand over (`{op_id}`); `409` with the missing items when not ready |
| `POST /rework` | Reopen Imagine on a new revision (`{op_id, reason}`); dependent evidence is marked stale |
| `GET /engine` | The studio's engine page, with the project's config embedded |

"A person only" means a caller sending `X-MetaForge-Surface: agent` is
refused with `403`. The full schemas are in the
[gateway API reference](reference/gateway-api.md).

## Not yet

- An editable sketch's shapes are lines, arcs, circles, ellipses and smooth
  curves; there are no angle or point-to-point distance dimensions yet.
  On the 3D part the depth, a revolve's angle, a circle's confirmed radius
  and a line's confirmed length have drag handles; an arc's radius and the
  distance between two points do not yet. A generated (raster) image cannot be made
  editable yet: only strokes drawn on the stage.
- Generated concepts: no configured provider streams partial images, so
  there is no progressive preview. Front, side and top views are not
  proposed together. Facts are read from text references and images, not
  from PDFs.
- Planes are Front, Side and Top, or a flat surface of a validated part;
  curved surfaces, and offset or custom planes, are later steps.
- Pen pressure is recorded with each point but not yet drawn as varying
  width.

- A 3D concept is one model from one image or text; the multiview source
  (front, side, back) the tool supports is not offered yet.
- Readiness is read by polling every five seconds, not pushed.
- Marking a dimension directly on a photo or the model, and calibrated camera
  measurement, are not built; measurements are typed, with their source.
- Comparing two revisions side by side is in the Engineering view's revision
  tools, not in Create.
- Finding manufacturers, quotes, orders and payment need a connector; Make
  says so rather than imitating one.
- The studio frame loads its engine page by address, so it cannot send a
  bearer token: on a gateway that requires sign-in the engine page has to be
  reachable without one (for example through a short-lived signed address).
- The studio snapshot (`fg:snapshot`) carries the `collections` the studio
  route returns; a gateway that does not return them sends an empty snapshot,
  and the page starts from its embedded config.
