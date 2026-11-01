---
name: check_tolerance
description: Check a part's toleranced dimensions against what the chosen manufacturing process can actually hold, with a capability index per dimension, DFM flags for too-tight bands and sub-minimum features, and an optional RSS stack-up, then record the verdict in the twin. Use when the user asks whether tolerances are achievable, whether a part can be machined or printed to spec, wants a DFM or tolerance review, or asks about a tolerance stack-up on a committed part.
domain: mechanical
---

# check_tolerance

Decide, dimension by dimension, whether the tolerance a designer asked for is
one the stated process can hold, and say plainly which ones it cannot. This is
arithmetic on numbers the user supplies; there is no MetaForge tool that runs
it. You do the calculation in the open, show the numbers, and record the
result against the part so it stays with the design.

## When to use it

- "Can we CNC the bore to 12 H7 and the slot to +/- 0.02?"
- "Is +/- 0.1 realistic on an FDM-printed bracket?"
- "Review the tolerances on the housing before we send it out."
- "What does the stack-up of these four spacers come to?"

Not for:

- Whether the part survives its load: `run_fea`.
- Creating or changing geometry: `generate_cad` / `generate_cad_ir`.
- GD&T form, profile or position tolerances, thermal growth or fits from a
  standard table: none of these are modelled (see Limits). Say so.

## Tools and profile

The calculation is done by you. The tools only find the part and keep the
record.

| Step | Tool | Served on |
|---|---|---|
| Check the connection | `health.check` | every profile |
| Find the part | `project.open`, `twin.find_by_property`, `twin.get_node` | every profile |
| Read real geometry, if needed | `twin.stage_work_product_file`, `freecad.describe_step_file`, `freecad.get_properties` | `mechanical` |
| Record the review | `twin.record_document` | `core`, `simulation` |
| Record a tolerance decision | `twin.record_decision` | every profile |

`twin.record_document` is served on `core` and `simulation`, not on `mechanical`. There, record
what you can with `twin.record_decision` and tell the user the full review
table was not stored.

## Inputs you need before you start

Ask the user for all of these. A process capability you pick yourself makes
the whole verdict your guess. Never default the process or its numbers.

| Input | Why it matters | Example |
|---|---|---|
| The part | The review is recorded against its node | `Pump Housing` or its node id |
| Each dimension: id, feature name, nominal (mm), upper and lower deviation (mm) | The thing being checked | D3 `bore_diameter` 12.000 +0.018 / 0.000 |
| Process name | Names what the numbers describe | `cnc_milling`, `3d_printing_fdm` |
| Achievable tolerance for that process (mm) | Drives every capability number | 0.05 |
| Is that figure a +/- value or a total band? | Changes Cp by a factor of 2 | "+/- 0.05" |
| Minimum feature size (mm), if the user has one | Flags features too small to make | 0.8 for FDM |
| Maximum aspect ratio (depth / width), if any | Flags deep narrow features | 4 |
| Whether to run a stack-up, and the closing dimension's limit | A stack-up needs something to compare against | gap must stay >= 0.2 mm |

The skill's own model treats "achievable tolerance" as a 3-sigma value of
the process. Confirm with the user that their figure means that (or convert
it with them) before calculating.

## Procedure

### 1. Find the part

1. `health.check`, `project.open`, then find the part's node with
   `twin.find_by_property` or `twin.get_node`. Note its node id and revision.
   If it does not exist, stop: the review must be pinned to a real part.
2. Optional: if the user wants nominal sizes confirmed against the model,
   `twin.stage_work_product_file` then `freecad.describe_step_file` gives the
   bounding box of each body. That confirms outer sizes only; a bore or slot
   width still has to come from the user or a drawing.

### 2. Calculate each dimension

For each dimension, with `a` = achievable tolerance and `band` = upper
deviation minus lower deviation:

- Process sigma = a / 3
- Capability index Cp = band / (6 x sigma) = band / (2 x a)
- Status: Cp >= 1.33 is **pass**, 1.00 <= Cp < 1.33 is **warning**
  (marginal), Cp < 1.00 is **fail**

Then the DFM flags:

| Flag | Rule | Severity |
|---|---|---|
| too_tight | band < a | error |
| below_min_feature | nominal < minimum feature size (only when given) | error |
| aspect_ratio_exceeded | nominal / band > maximum aspect ratio (only when given) | warning |
| marginal capability | 1.00 <= Cp < 1.33 and not already too_tight | warning |

The aspect-ratio rule uses nominal divided by band as a stand-in for depth
over width. It is a crude proxy; if the user gives the real depth and width
of a feature, compare those instead and say which you used.

Worked example: band 0.018, a = 0.05 gives Cp = 0.018 / 0.10 = 0.18, a fail,
and too_tight (0.018 < 0.05). Recommend widening to at least the process
capability or moving that feature to a finer process (reaming, grinding).

### 3. Stack-up, if asked

- Worst case = sum of the bands.
- RSS = square root of the sum of the squared bands.
- Compare both with the closing-dimension limit the user gave. Report both
  numbers; RSS assumes independent, centred, normally distributed
  dimensions, so say that.
- The skill's own heuristic flags a stack when RSS exceeds 75 % of worst
  case. Report it if it fires, but the user's closing limit is the real test.

### 4. Overall verdict

- Any dimension failing: **fail**. Otherwise any warning: **marginal**.
  Otherwise **pass**.
- No dimensions given: no verdict. Say "nothing checked", never "pass".

### 5. Record

- If `twin.record_document` is in your tool list, record the review with
  `document_type: "documentation"`, `name` such as
  `Pump Housing tolerance review, rev 3`, and `content` holding: the process
  and its stated numbers with their source, the per-dimension table
  (nominal, band, Cp, status), every flag with its recommendation, the
  stack-up numbers, the overall verdict, and that the calculation was done by
  the client, not by a MetaForge tool. Pass `project_id`.
- If it is not in your list, say the review table was not recorded on this
  connection.
- When the user decides to change a tolerance or the process, record it with
  `twin.record_decision` (`title`, `rationale`, `alternatives` such as
  "widen to +/- 0.05" versus "ream to H7").

### 6. Report

The per-dimension table, the flags with recommendations, the stack-up, the
overall verdict, where each process number came from, and what was recorded
with its node id.

## Checks before you report

- [ ] The process and every capability number came from the user
- [ ] The +/- versus total-band question was settled before calculating
- [ ] Band = upper minus lower deviation, signs kept (lower is usually negative)
- [ ] Cp and every flag shown with its numbers, not just a status word
- [ ] Empty input reported as "nothing checked"
- [ ] Recorded against the part, or the user told it was not

## Failure handling

| Symptom | Likely cause | What to do |
|---|---|---|
| Part not found | Wrong name or project | Ask the user; do not review an unidentified part. |
| Upper deviation below lower | Values swapped | Ask the user which is which; do not swap silently. |
| Every Cp is below 0.5 | Process figure is a total band, or wrong units (um vs mm) | Confirm units and meaning with the user. |
| `-32001` naming `freecad` | Adapter down while reading geometry | Skip the geometry read; the calculation does not need it. Tell the user. |
| `twin.record_document` held for approval | Writes need a person | Tell the user where it waits; do not retry or reword. |
| `twin.record_document` missing | Profile without it (e.g. `mechanical`) | Record the decision only, and say the table was not stored. |

## Limits

- Bilateral and unilateral size tolerances only. No GD&T (flatness,
  position, profile), no fits from ISO 286 tables, no thermal expansion, no
  surface-finish check even if the process states an Ra.
- Cp only, not Cpk: it assumes the process is centred on nominal.
- The material plays no part in this calculation; do not imply it does.
- The result is only as good as the process numbers. Name their source.
