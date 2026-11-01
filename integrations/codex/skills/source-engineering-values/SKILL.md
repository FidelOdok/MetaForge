---
name: source-engineering-values
description: Find an engineering value a design needs (a dimension, a height, a material, a section size, a thickness, a tolerance, a load) from a real source instead of guessing, and record the value together with where it came from. Use whenever a phase or a question needs a number or a material the user has not given, before you write it into a requirement, a CAD model or an analysis, and when choosing a purchasable part or stock size. Covers project documents, standards, past work, component search and selection, analysis, measurement, and stated assumptions.
---

# Source engineering values

A design is only as trustworthy as its numbers. A table height with no
source and a table height from a standard look identical in a CAD model and
at a gate, so MetaForge records **each value together with its source**, and
refuses a value whose source a reviewer could not find again.

Your job: when a value is missing, look for it in the order below, take the
first source that genuinely answers it, and record it. Never fill a gap with
a plausible number and move on.

## What only the user decides

Some values are choices, not facts. They come **only** from the user; no
standard or search answers them:

- budget and target cost
- production quantity
- which machines, processes and suppliers they can use
- the look: style, finish, colour
- any value they have already stated (theirs wins over every other source)

Ask for these. "Unknown" is a valid answer: record it as unknown, and say
which later steps it will block.

## The sources, in order

Try them in this order. Stop at the first one that answers the question.

| # | Source | Look with | `source.kind` and its fields |
|---|---|---|---|
| 1 | The user said it | the conversation, the intent, `twin.thread_for` | `user` |
| 2 | The project's documents (PRD, drawing, datasheet, earlier design) | `knowledge.search`, then `knowledge.extract` for typed values with citations | `project_document`: `document`, plus `locator` (page, section, table) |
| 3 | A standard or reference | `knowledge.search` first (ingested standards), then `web.search` / `web.fetch` | `standard`: `standard` (for example `EN 12521:2015`), plus `clause` and `url` |
| 4 | Past work | `memory.retrieve_similar_experience`, `twin.find_by_property` | `past_work`: `node_id`, or `project_id` + `ref` |
| 5 | A real component or stock size | `component.search_parametric`, `component.search_intent`, `distributors.resolve_offers` | `component`: `mpn` (a part) or `stock` (a stock size), plus `distributor`, `url` |
| 6 | Worked out from other values | `calculix.run_fea`, `twin.optimize_parameter`, a hand calculation you state | `analysis`: `evidence` (a `twin.record_evidence` id), or `method` + `inputs` |
| 7 | Measured from an existing object | `freecad.import_step`, `freecad.describe_step_file`, `freecad.measure` | `measurement`: `artefact` (the file or node), plus `tool` |
| 8 | None of the above | your engineering judgement, stated | `assumption`: `rationale` |

A source must actually say the value. A standard that covers tables but says
nothing about height is not a source for height. Cite what you read, not
what you expect it to say. If `web.fetch` could not open the page, you did
not read it.

### Assumptions

An assumption is allowed only when sources 1 to 7 genuinely do not answer
the question, and only as an assumption: kind `assumption`, a `rationale`
saying why the default is reasonable, and the value marked for review. The
twin stamps it `needs_review`. Tell the person, at the next gate, every value
that rests on an assumption, so they can confirm or replace it. Never
present an assumption as a fact.

## Recording a value

Record each value once with `twin.record_engineering_entity`:

```json
{
  "entity_type": "design_value",
  "title": "table_height",
  "statement": "Table height 740 mm, mid-range of the seated-height band in the reference.",
  "item_key": "VAL-TABLE-HEIGHT",
  "parent_refs": ["NEED-USABLE-TOP"],
  "extra": {
    "name": "table_height",
    "value": 740,
    "unit": "mm",
    "source": {
      "kind": "standard",
      "standard": "<the standard or reference you actually read>",
      "clause": "<the clause or table>",
      "url": "<where you read it>"
    }
  },
  "project_id": "<project>"
}
```

- `name`, `value` and `source` are required, and the source must carry its
  kind's fields. A value without them is refused with the reason; fix it and
  record again rather than dropping the source.
- `unit` must be a unit the twin recognises (`mm`, `kg`, `N`, ...). Leave it
  out for a dimensionless value such as a safety factor or a material name.
- Link the value to the need or requirement it serves with `parent_refs`.
- A value that changes, or that gets a better source, is a new revision: pass
  the same `item_key` and a `change_reason`.
- A requirement's limit records its source too: in `twin.record_constraint_set`
  give each constraint a `limit_source` with the same `{kind, ...}` shape.

Then use the value: in the CAD parameters, the analysis inputs, the
requirement. The record is what lets a reviewer trace a dimension in the
model back to where it came from.

## Choosing a component or stock size

When the value is a purchasable thing (a tube section, a sheet thickness, a
fastener, a caster, a bearing, a board):

1. **State what it must do first**, from the requirements: load, size limits,
   material, environment. These are the search bounds.
2. **Search.** `component.search_parametric` with those bounds, or
   `component.search_intent` when you only have the goal ("leg tube for a
   table, 1000 N corner load"). For a stock material, search for the section
   or sheet.
3. **Check availability and price.** `distributors.resolve_offers` for the
   candidates you would actually pick.
4. **Pick one, and say why**: which bound it meets, what it beats the others
   on, and what you gave up. Keep the runners-up.
5. **Record the selection.** `twin.record_component_selection` with the part
   number, manufacturer, distributor, price and datasheet. Then record a
   `design_value` for each number you take from it (the section's width and
   wall, the thickness), with source kind `component` and the `mpn` or
   `stock` item, so the geometry traces back to the part.
6. Record the choice itself with `twin.record_decision`: the alternatives
   considered and why this one won.

If no part fits the bounds, say so and say which bound to relax. Do not pick
the nearest part and quietly change the requirement.

## Worked example: a table

Values a table design needs, and where each would come from:

| Value | Likely source |
|---|---|
| Loads, safety factor | the user (already in the intent) |
| Top length and width | the user, or a seating reference (width per seated person) as an assumption to confirm |
| Table height | a furniture or ergonomics standard |
| Strength and stability tests | the furniture standard for tables (test loads and method) |
| Leg section | a real stock tube from component search, sized by analysis |
| Top thickness | stock sheet sizes, then `twin.optimize_parameter` against the deflection limit |
| Material | the user, or chosen with the route, recorded with why |
| Budget, quantity, look | the user only |

## When a tool is missing

The sources need tools from more than one profile. If a tool you need is not
on this connection, say which tool and which source it was for, and suggest
the profile that serves it, or the full tool set (clear the plugin's
`tool_profile`). Do not substitute a weaker source silently: a value you
would have taken from a standard does not become an assumption because
`web.fetch` was missing. Say so, and record it as unknown or ask.

## At a gate

When you put a gate to the person (`flow.await_gate`), list:

- every value the phase used, and its source in one line each;
- every value resting on an `assumption`, first, so they can confirm or
  replace it;
- every value still unknown, and what it blocks.
