# MetaForge Wiki

A compounding knowledge base for this codebase, following Andrej Karpathy's ["LLM wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern.

## The core idea

A RAG-style setup re-derives an answer from raw sources every time you ask. This wiki instead **compiles knowledge once and keeps it current**: an agent reads a source (here, the codebase, git history, docs/, or a proven-out personal-memory note), extracts what's durable, and files it into a structured, cross-linked markdown page — updating existing pages when new information contradicts or refines them. The wiki gets richer with every session, not just longer.

## The three layers

1. **Raw sources** — immutable, read but never modified: the codebase itself, git history, `docs/` (as-built architecture), the MetaForge-Planner repo (forward plans), and any personal Claude Code memory notes once they've proven durable.
2. **The wiki** — this directory. Entity/concept pages, `index.md`, `log.md`. Agents own this layer entirely; humans read it and can correct it, but the maintenance burden is the agent's job.
3. **The schema** — `CLAUDE.md`'s "Compounding knowledge — this repo's `wiki/`" section. It defines the Ingest / Query / Lint operations below and is the file every agent reads to know how to behave here.

## Operations

- **Ingest** — when you learn something durable and non-obvious (a gotcha, a piece of docs/reality drift, a "look here not there" correction), write or update a page, update `index.md`, and append a `log.md` entry. Don't wait to be asked.
- **Query** — before working in an unfamiliar part of the repo, read `index.md` first to find relevant pages, then drill in. If answering a question produces something worth keeping (a comparison, a root-cause writeup, a synthesis) — file it back as a page instead of letting it evaporate into chat history.
- **Lint** — periodically check for: pages contradicting each other, claims a newer page has superseded, orphan pages with no inbound links, concepts mentioned repeatedly but lacking their own page, and `updated:` dates old enough that the underlying code may have moved on. Fix what you find; log the pass.

## Conventions

- One page per entity/concept, plain markdown, `kebab-case.md` filenames, H1 title matching the entity name.
- Every page starts with `---\nupdated: YYYY-MM-DD\n---` frontmatter — the one signal a lint pass has for staleness.
- Cross-link with relative markdown links (`[Twin Core](digital-twin.md)`) so pages render as real links on GitHub and read as a graph, not a list.
- Keep pages short — a note, not an essay. Link out to `docs/` for the full architectural story.
- Correct or delete a page the moment you find it's wrong. A stale wiki page is worse than no page.

Start at [`index.md`](index.md) for the full catalog, or [`log.md`](log.md) for the history of what's been ingested.
