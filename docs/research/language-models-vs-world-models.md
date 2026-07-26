# Where MetaForge Stands: Language Models, Not (Yet) World Models

> Perspective piece — not an architecture spec. See [`docs/architecture.md`](../architecture.md)
> for the as-built system and [`docs/roadmap.md`](../roadmap.md) for what's actually planned.

Every few months AI picks a new axis to argue about. Right now it's **language models
vs. world models** — and because MetaForge sits at the intersection of AI and physical
engineering, it's a fair question to ask: which one is MetaForge actually built on, and
does the other one matter to us?

Short answer: MetaForge today runs entirely on **language models**. "World models" are a
genuinely different, fast-moving branch of AI research that MetaForge doesn't use — and
for the job MetaForge does, that's currently the correct call, not a gap. Here's why.

## What MetaForge actually runs on today

Strip away the layer diagram and MetaForge's core loop is: an LLM reads structured
context, decides which deterministic tool to call, and a real engineering tool does the
actual work.

- **Domain agents** (`domain_agents/mechanical`, `electronics`, `firmware`, `simulation`,
  …) are [PydanticAI](https://ai.pydantic.dev/) agents — a thin, typed wrapper around a
  language model (Claude or GPT via a unified provider abstraction). The LLM's job is
  reasoning and tool selection, not simulation.
- Agents never touch engineering tools directly. Every action goes through the **Skill
  System** and the **MCP protocol layer** to a containerized adapter — KiCad, FreeCAD,
  CalculiX, SPICE. The physics, the electrical rule checks, the mesh stress analysis —
  all of that is computed by the *actual* deterministic tool, not predicted by the model.
- All of it reads from and writes to the **Digital Twin**, a versioned graph that is the
  single source of truth. Nothing the LLM "imagines" becomes real until it's written to
  the graph, validated by the constraint engine, and — in Assistant Mode, the default —
  approved by a human.

So when people say "MetaForge uses AI," what's actually true is narrower and more
boring, in a good way: an LLM plans and narrates; boring, well-understood, decades-old
engineering software (SPICE, FEA solvers, ERC/DRC checkers) does the physics. The
**Prime Rule** — *if it can't be versioned, reviewed, and built, MetaForge doesn't output
it* — only holds because the thing computing the physics is deterministic and inspectable.
An LLM's job in this system is to route intent to the right deterministic process, not to
be the process.

## What "world models" are, and why they're a different thing

A **language model** is trained to predict the next token in text. A **world model** is
trained to predict the next *state* of an environment — the next video frame, the next
3D scene, the next physical configuration — given the current state and an action. Instead
of "what word comes next," the question is "what does the world look like one step from
now if I do this."

2025–2026 has been the paradigm's breakout stretch:

- **DeepMind's Genie 3** generates interactive, playable 3D environments in real time from
  a text or image prompt, with enough visual and physical consistency that it reportedly
  triggered urgency inside competing labs to add spatial reasoning to their own models.
- **World Labs' Marble** (from Fei-Fei Li's team) generates persistent, downloadable 3D
  environments from text, photos, or video — aimed at giving other systems a "world" to
  act in, not just a video to watch.
- **NVIDIA Cosmos** is a world-model platform aimed squarely at robotics and autonomous
  vehicles: generating synthetic, physics-aware training data and simulated environments
  at scale.
- **Yann LeCun** left Meta to found AMI Labs specifically to pursue world models (JEPA-style
  architectures) as what he argues is a more promising route to physical understanding
  than scaling language models further.

The common thread: these systems learn an *implicit, statistical* model of how a world
behaves, by watching enormous amounts of video or interaction data. You don't get a bill
of materials or a netlist out of a world model — you get a plausible next frame.

## Why MetaForge isn't (and, for now, shouldn't be) built on this

The distinction that matters for hardware isn't "which model is smarter" — it's **what
kind of answer you need**.

| | Language model (what MetaForge uses) | World model (Genie 3, Marble, Cosmos, JEPA) |
|---|---|---|
| Predicts | Next token / tool call / decision | Next physical or visual state |
| Grounded by | Real solvers (SPICE, CalculiX, KiCad ERC/DRC) it calls out to | Its own learned, implicit physics |
| Output | Structured, versioned work products (schematics, BOMs, code) | A frame, a scene, a trajectory |
| Determinism | High — same inputs to a skill give the same tool output | Low — generative, sampled |
| Reviewability | Git-diffable, human-approvable | Not natively diffable or auditable |

A PCB either meets its DRC rules or it doesn't; a beam either exceeds yield stress under
load or it doesn't. Those are questions with exact answers that a real solver already
computes correctly and a human can check line-by-line. A generative world model — even a
very good one — gives you a statistically plausible simulation, not a certified one.
MetaForge's Prime Rule requires the certified kind. Handing a "vibes-based" physics guess
to something that becomes an assembled circuit board is precisely the failure mode
MetaForge exists to prevent.

That's also why the comparison isn't really language models *vs.* world models for
MetaForge's purposes — it's language models *plus deterministic tools* vs. a single
learned simulator standing in for both reasoning and physics. MetaForge picked the former
because the latter can't yet produce a diffable, reviewable, sign-off-able artifact.

## Where it could get interesting later

None of this means world models are irrelevant to MetaForge's future — just that they'd
slot in as an *advisory* layer, never as the source of truth, in keeping with the
architectural invariant that the Digital Twin — not any model's internal state — owns all
design state:

- **Fast pre-screening before an expensive solve.** A learned model could suggest "this
  enclosure geometry is probably going to fail thermally" in milliseconds, letting an
  agent decide whether it's even worth queuing a full CalculiX run — a hint, not a verdict.
- **Synthetic data for skill calibration.** World-model-generated scenarios (à la NVIDIA
  Cosmos for robotics) could stress-test skills against edge cases that are expensive to
  collect from real hardware.
- **Assembly and bring-up simulation.** A robotics-style world model that understands
  physical manipulation could eventually help validate mechanical assembly sequences
  before a human ever picks up a part.

Every one of those stays firmly inside Assistant Mode's rule: propose, don't commit. The
constraint engine and a human still gate anything that reaches the Twin.

## The bottom line

As of today, MetaForge's "AI" is a language model doing structured reasoning and tool
orchestration on top of deterministic engineering software — not a learned simulator of
physical reality. World models are one of the most interesting AI research directions
going into 2026, but they solve a different problem (learning an implicit physics engine
from video) than the one MetaForge is chartered to solve (turning human intent into
versioned, reviewable, manufacturable hardware). If world models mature to the point of
producing certifiable, auditable outputs, they're a candidate for an advisory role
someday — not a replacement for the solvers, the Twin, or the human in the loop.

---

**Sources**

- [Genie (world model) — Wikipedia](https://en.wikipedia.org/wiki/Genie_(world_model))
- [DeepMind Genie 3, NVIDIA Cosmos Lead World Models Race — AI Business Weekly](https://aibusinessweekly.net/p/deepmind-genie-3-nvidia-cosmos-world-models-race-2026)
- [World Models Race 2026 — Introl Blog](https://introl.com/blog/world-models-race-agi-2026)
- [World Models 2026: Google, NVIDIA & LeCun Build AI That Understands Physics — AI.cc](https://www.ai.cc/blogs/world-models-2026-google-nvidia-physical-ai-breakthroughs/)
- [Fei-Fei Li's World Labs Splits World Model Into Three Types: Marble Targets Simulation Linchpin — Tech Times](https://www.techtimes.com/articles/317927/20260606/feifei-lis-world-labs-splits-world-model-three-types-marble-targets-simulation-linchpin.htm)
- [World Model for Robot Learning: A Comprehensive Survey](https://arxiv.org/pdf/2605.00080)
