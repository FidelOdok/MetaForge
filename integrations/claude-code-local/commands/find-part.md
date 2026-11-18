---
description: Find a real part for a need: the library first, then distributors, then the web
---

Find a real, buyable part for what the person described, following the `find-component` skill.

Say the need in a few words (its job and key limits, or a part number) and call `component.find` once. Pass `region` when the person wants local supply. Read every list before choosing: `library` (use a fitting library part by its `name`; never register it again), `distributors` (prefer in production, in stock, with a datasheet; a candidate marked `in_library` is already held), and `web` (pages, not parts: read one with `web.fetch` before trusting it). Tell the person why a `blocked` part is blocked before suggesting it.

To add a distributor part, read its datasheet for the outline (length, width, height in mm) and pins, each with the page it came from, then call `component.adopt` once. A part found only on the web is registered with `library.register_component`, sourced to the page you read. Never estimate a size or a pin: leave out what you could not find. Both writes wait for the person's approval; say so rather than claiming the part is added.

Report what `searched` says: what each source found, or why it could not answer. Nothing found is a normal answer; suggest other words rather than inventing a part.
