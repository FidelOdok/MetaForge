---
name: find-component
description: Find a real, buyable part for a need (a board, module, chip, sensor, connector, battery or LED), reuse it from the MetaForge library when it is there, and otherwise pick one from the live distributors and add it to the library once, with its datasheet sizes and pins and a dated offer. Use when a design needs a part ("an ESP32-S3 module", "a 12-pixel LED ring", "a 24 V BLDC driver for 5 A"), when a studio part has no real part chosen yet, or when the user asks what part to use.
---

# Find a component

One tool does the searching: `component.find`. It searches the MetaForge library first,
then every configured distributor, and returns two ranked lists. Each candidate says where
it came from, whether the library already holds it, whether its maker or every seller is
blocked, and what is still unknown.

## Steps

1. **Say the need in a few words**, with the part's job and its key limits: `"ESP32-S3
   module USB-C"`, `"WS2812B ring 12 pixel"`, `"BLDC driver 24V 5A"`. A part number works
   too. Pass `region` (for example `UK`) when the owner wants local supply.
2. **Call `component.find` once.** Read every list before choosing.
   - **A library part fits:** use it. Link it by its library `name` (in a studio, the
     part's `"library"`); the library fills its maker, order code, datasheet, size, model
     and price. Do not read the entry unless you need something in it to decide.
   - **A part is `blocked`:** do not choose it without telling the person why it is blocked
     and what you would use instead.
   - **Only distributor parts fit:** prefer one in production, in stock, with a datasheet.
     Note which distributor you will record (`source`).
   - **Only web pages fit** (`web`): no distributor carries the part, as for many boards
     sold only by their maker. A web result is a page, not a part: read it with `web.fetch`.
     If it is the part, register it with `library.register_component` (maker, order code,
     datasheet, size and pins, each sourced to the page you read), not `component.adopt`,
     which needs a distributor's offer. `web: "on"` searches the web even when the library or
     the distributors found something; each web search is a paid API call, so leave it on
     `auto` unless the person asks.
3. **Read the datasheet** of the distributor part you choose (its `datasheet_url`, with
   `web.fetch` or `knowledge.extract`) for what the library needs and the distributor does not
   give: its outline (length, width, height in mm) and its pins. Write down the page each value
   came from. A value you could not find stays out: never estimate a size or a pin.
4. **Adopt it once** with `component.adopt`: `mpn`, `manufacturer`, `distributor`, `family`
   (`module` for a bought board or module, `electronic` for a single chip, button or
   connector, `battery`, `other`), the `specs` you read (`length_mm`, `width_mm`,
   `height_mm`, each `{value, unit: "mm", source_kind: "datasheet", source_ref: "<URL, page>"}`)
   and the `interfaces` you read (`[{id, bus, dir}]`). It records the distributor's live price
   breaks and stock as a dated offer. It is held for the person's approval; once approved, every
   later project finds the part in the library.
5. **Record the choice** where the design needs it: link the part by its library name in the
   studio, or `twin.select_component` with the candidates you compared when a decision should
   show the alternatives you rejected and why.

## What not to do

- Do not register a part the library already holds: `component.find` marks a distributor
  candidate `in_library` when the library has that order code; use the library entry.
- Do not list the whole library (`library.list_components`) to look for a part: it is slow and
  large. `component.find` and `library.search` search it.
- Do not copy a distributor's description into a spec. Sizes and pins come from the datasheet.

## When nothing fits

`component.find` says what it searched: the library, and each distributor with how many it
found, or why it could not answer (no credentials, timed out). Nothing found is a normal
answer: try other words, a part number, or fewer limits, and say so to the person rather than
inventing a part.
