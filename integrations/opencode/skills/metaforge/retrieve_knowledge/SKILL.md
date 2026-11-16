---
name: retrieve_knowledge
description: Search MetaForge's project knowledge layer (ingested datasheets, design rules, failure notes, past decisions) and answer with citations to the source and heading each fact came from. Use when the user asks what a datasheet, standard or earlier note says, asks "do we have anything on", "what did we learn about" or "what is the limit for" a part or rule, or when a design step needs a sourced value rather than a remembered one.
domain: shared
---

# retrieve_knowledge

Find what the project already knows and quote it with its source, so an
answer can be checked. The knowledge layer returns ranked text chunks, each
carrying the `source_path` and heading it came from. Your job is to ask a
good question, scope it to the right project, read the hits critically, and
report only what they actually say.

## When to use it

- "What is the BMI270's maximum supply voltage according to the datasheet we
  ingested?"
- "Do we have any design rules on minimum trace width?"
- "What went wrong with the last motor mount?"
- "Find our notes on why we picked CAN over RS-485."
- A later step (FEA, component selection) needs a sourced number and the
  user says it should be in the project's documents.

Not for:

- Adding a document (use the `ingest_knowledge` skill).
- A twin node you can name by property, such as a BOM item by MPN (use
  `twin.find_by_property`; it is exact and structured).
- Recorded decisions and their alternatives as typed records (read
  `metaforge://twin/decisions/<project_id>`).
- Past agent runs on a similar goal (`memory.retrieve_similar_experience`,
  `core` profile).
- Stock, price or lifecycle of a part (use the distributor tools; a document
  is stale for that).
- Typed values by MPN from the current datasheet (`knowledge.extract`, only
  served when the client connects with no profile).

## Tools and profile

| Step | Tool | Profile |
|---|---|---|
| Open the project | `project.open` | every profile |
| Search | `knowledge.search` | every profile |
| What sources exist | resource `metaforge://knowledge/sources` | any |
| One source's chunks | resource `metaforge://knowledge/sources/{id}` | any |
| Exact twin lookups | `twin.find_by_property`, `twin.get_node` | every profile |
| Similar past runs | `memory.retrieve_similar_experience` | `core` |
| Nothing ingested: public sources | `web.search`, `web.fetch` | `core` |

`knowledge.search` is on every profile, so this skill works on any
connection. The web tools are `core` only.

## Inputs you need before you start

| Input | Why it matters | Example |
|---|---|---|
| The question | A specific question retrieves better than a keyword | "maximum VDD for BMI270" |
| Project | Without `project_id` the search runs against a shared `default` tenant, not the project | id from `project.open` |
| Category (optional) | Narrows results; must be a server value | `component` |
| Whether old revisions count | Superseded datasheet revisions are hidden by default | audit question: yes |

If the user's question is vague ("anything about power?"), ask what decision
it feeds before searching. Do not fill a gap in the results with a value from
memory.

## Procedure

### 1. Scope to the project

Call `project.open` with the user's project name as `query`; ask if several
match. Pass the `project_id` on every `knowledge.search` call, even after
`session.start`: the search reads the project from its argument, and the
session's scope does not reliably reach it.

### 2. Search

Call `knowledge.search` with:

- `query`: a natural-language question with the distinguishing tokens in it
  (part number, standard number, the parameter name).
- `project_id`: from step 1.
- `top_k`: 5 by default, up to 50. Raise it when the answer may be spread
  across a long document.
- `knowledge_type` (optional): one of `component`, `constraint`, `failure`,
  `design_decision`, `session`. Any other value is rejected. Leave it out if
  you are not sure how the source was categorised.
- `filters` (optional): exact-match on metadata keys, with scalar values
  only, for example `{"source_path": "https://.../bmi270-ds000.pdf"}` or
  `{"mpn": "BMI270"}`. Keys are ANDed.
- `include_historical: true` only when the user asks about an older
  revision or wants an audit trail.

### 3. Read the hits

Each hit carries `content`, `similarity_score`, `source_path`, `heading`,
`chunk_index`, `total_chunks`, `knowledge_type`, `metadata` and
`source_work_product_id`.

1. Rank by relevance to the question, not by `similarity_score` alone. A
   high score on a chunk that does not state the value is not an answer.
2. Check the hit actually contains the fact. Quote the sentence or table row;
   do not paraphrase a number.
3. Check conditions: a datasheet maximum usually has a temperature, a
   supply or a package attached. Report them with the value.
4. When two sources disagree, report both with their sources. Do not pick.
5. When the answer is near a chunk boundary (a table split across chunks),
   read the neighbouring chunks through
   `metaforge://knowledge/sources/{id}`, where `{id}` is the URL-encoded
   `source_path`.

### 4. If nothing relevant comes back

1. Retry once with different wording, or without the `knowledge_type`
   filter.
2. Read `metaforge://knowledge/sources` to see whether the source was ever
   ingested for this project. An empty `hits` list is "not in the knowledge
   layer", not "the value does not exist".
3. Tell the user. Offer to find a public source with `web.search` and
   `web.fetch`, and to ingest it with the `ingest_knowledge` skill if they
   want it kept. A value read from the web is cited to its URL and is not
   presented as project knowledge until it has been ingested.

### 5. Report

- The answer, quoted, with units and conditions.
- For each fact: `source_path`, `heading`, and page or table if the chunk
  shows it.
- What you searched for and what was not found.
- Whether superseded revisions were included.

## Checks before you report

- [ ] `project_id` was passed
- [ ] Every number in the answer appears in a hit you can quote
- [ ] Every fact carries its `source_path` and `heading`
- [ ] Conditions (temperature, supply, package) reported with limits
- [ ] Conflicting sources reported side by side
- [ ] "Not found" stated as not found in the knowledge layer, with what was
      tried

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| "'knowledge_type' must be one of ..." | A value outside the server's five | Use an allowed value or drop the filter. |
| A filter error naming `filters.<key>` | A list or object as a filter value | Use a single scalar value per key. |
| Empty `hits` for a source you know was ingested | Missing or different `project_id`, a type filter, or the source was superseded | Search again with `project_id`, no type filter, then `include_historical: true`. |
| Hits from another project | `project_id` not passed | Search again with it. |
| Every search fails, for every query | The knowledge backend is down or a corrupt row breaks retrieval | Report the error text to the user; do not loop. A failed call is not "no results". |
| The resource read returns "No knowledge source registered" | The `{id}` was not the exact URL-encoded `source_path` | Copy `source_path` from the sources list and encode it. |
| Web tools missing | Not on the `core` profile | Say what was not found and that a web lookup needs `?profile=core`. |

## Limits

- Search is semantic and returns chunks, not answers. It can rank a
  near-miss above the right chunk, and it cannot read tables or figures that
  were not ingested as text.
- Only ingested text is searchable. Nothing in the twin's work products is
  searchable here unless someone ingested it.
- Superseded datasheet revisions are hidden unless `include_historical` is
  true.
- A retrieved value is evidence of what a document says, not verification
  that the design meets it.
