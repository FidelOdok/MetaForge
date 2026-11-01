---
sidebar_position: 7
---

# Parts list + Files

The parts list answers "what do we buy and what does it cost?"; Files answers "which design files back it up, and are they in sync?" Both follow the project picker in the top bar.

## Parts list

**What you can do here:**

- **See totals** in the header: how many parts and the combined cost.
- **Find parts** with the search box (searches reference, part number, description, maker) and the availability filter (Available, Low Stock, Out of Stock, Alternate Needed). An active filter tells you how many of the total are shown.
- **Sort** by selecting any column header (Reference, Part Number, Description, Maker, Quantity, Unit Price, Status) — select again to reverse the order.
- **Buy or verify** per row: the part number opens the shop page, the notes icon opens the datasheet, prices show in their own currency, and the status label flags availability. Please report broken shop or datasheet links.
- **Export** with the **CSV** button (top right) — downloads a spreadsheet of exactly the rows you currently see, including links.

Use it as your pre-order check: filter to *Out of Stock* and *Alternate Needed* before placing an order. An empty project shows *No components* (select a project or run an agent first); a filter with no hits shows *No matches*.

## Files

Your design files registry: every file linked to your project, grouped by the tool it came from (electronics, mechanical CAD, circuit simulation, or other).

**What you can do here:**

- **Find a file** with the search box and the tool filter chips (*all, electronics, mechanical, simulation, other*). The panel header tells you how many of the total are shown.
- **Act on a file** in the file list: the label tells you which tool owns it, the coloured dot tells you its state (green = in sync, amber = changed since last sync, grey = disconnected), plus when it was last synced. Hover a row for **Sync** (pull the latest version) and **Unlink** (remove the link; the design item itself is kept). Unlinking happens immediately.
- **Review health** on the side: **By Tool** bars (how many files per tool) and **Sync Status** bars (share of synced / changed / disconnected files). The **Sync Pipeline** lists the most recently synced files.
- **Refresh** with the header button.

If no files are linked yet you will see *No source files linked yet*; if nothing has synced you will see *No sync activity yet* — both are normal empty states, not errors.
