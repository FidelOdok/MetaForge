---
name: quote-and-order
description: Get real prices from manufacturing vendors (PCB houses, 3D-print and CNC services, distributors) connected to MetaForge, record each quote in the twin, and place an order only when the person approves it. Use when the user asks what a board or part would cost to have made, wants a quote from a vendor, wants to compare vendors, or asks to order. Do not use for parts on the person's own machines; that is the manage-machines skill.
---

# Quote and order

MetaForge reaches vendors through connectors on the gateway. A quote is the
vendor's own price, recorded in the twin with the vendor, the mode and the
date. An order is a purchase and always waits for the person.

## Rules

1. **Check what is connected first.** `vendor.list_connectors` shows each
   connector, what it can quote (`services`), its `mode` and whether its
   credential works (`auth`). Do not quote through a connector whose `auth` is
   not `ok`; tell the person what `detail` says (usually a missing
   `METAFORGE_VENDOR_*` key on the gateway).
2. **Say the mode every time.** `sandbox` means nothing is bought and prices
   may be test figures; `production` means real orders and real money. Never
   present a sandbox price as what the part will cost.
3. **Quote what exists.** Items are files already in the twin
   (`work_product_id`: Gerbers, STEP, STL) or part numbers. If the files are
   not there yet, say so rather than describing them.
4. **Never order without the person.** `vendor.place_order` is held for their
   approval every time. Show the quote (lines, total, currency, lead time,
   validity, mode) and ask before calling it. Never retry a refused or
   timed-out order without asking.
5. **Report failures as they are.** Errors carry a kind: `auth`, `config`,
   `rate_limited`, `unreachable`, `refused`, `protocol`, `not_supported`. Name
   it; do not retry `auth` or `config`, and wait before retrying
   `rate_limited`.

## Steps

1. `vendor.list_connectors`.
2. `vendor.quote` with `connector_id`, `service` (for example
   `pcb_fabrication`, `additive`, `cnc`), `items` and `quantity`, plus the
   `project_id` so the record lands in the project. It is held for approval
   because it writes the quote to the twin.
3. Compare quotes across connectors if there is more than one. In a design
   flow's route phase, a quote is a sourced cost for
   `rank_manufacturing_processes` (cite the quote's twin record).
4. When the person decides, `vendor.place_order` with the `quote_id`. It is
   refused if the quote expired, was already ordered, or the connector's mode
   changed since; quote again then.
5. `vendor.order_status` to follow it.

## Vendors with no API

Many services cannot be quoted or ordered by API (AISLER for PCBs,
Craftcloud for printing, Xometry for CNC, sheet metal and moulding,
SendCutSend for laser-cut and bent parts). For those:

1. `vendor.handoffs` with the `service` and the `work_product_ids` of the
   files you would send. It returns each vendor that takes the service, its
   upload page, the formats it accepts, which of the files fit, and a
   `download_path` on the gateway for each file.
2. A vendor with no usable file needs one exported first (for example a STEP
   from the CAD model, or a DXF of a flat part). Say which.
3. Give the person the page and the files. Nothing is sent by MetaForge.
   Tell them to confirm the accepted formats on upload: the catalogue is
   MetaForge's own, not read live from the vendor.
4. A price they get there is a real quote from that vendor; record it with
   the vendor and date if they share it.

## Checklist

- [ ] connector's auth is ok and its mode is stated
- [ ] quote recorded with the project
- [ ] the person approved the order; nothing retried without them
