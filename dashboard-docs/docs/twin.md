---
sidebar_position: 6
---

# Digital twin

The digital twin is a full-width workspace for exploring your design. Use it to find a part, look at its 3D shape or how it connects to other parts, check its files and history, and ask questions about it — all for the project selected in the top bar.

## Finding something

- **Search** in the header filters by name or type.
- **Explorer** (left panel) groups items by area. Use **All / Needs attention** to narrow down to items that are failing, stale, or have problems. Collapse the panel when you need more room for the 3D view.
- **Status strip** under the header counts items needing attention, total items, and items with no connections. Select the attention count to filter to just those. **Start design run** jumps to starting a new run for the current project.

If the connection is down you will see a *Twin data unavailable* message with a link to Settings instead of a blank canvas. If the project is empty you will see an *Empty twin* message with an **Import work product** shortcut.

## The four views: Graph, Model, Sim, Assembly

The header buttons switch the centre of the screen:

- **Graph** — a diagram of items and how they connect. Select any item to see its details. Best for answering "what connects to what?"
- **Model** — the 3D viewer for the selected item. The model loads automatically when you select it. Drag to orbit, use the parts tree to focus on one piece, try **Exploded view** to pull the assembly apart, and use the camera button to save a picture.
- **Sim** — the same 3D view plus a **Robotics physics** bar with **Run / Stop**. It activates once a robot item is loaded — pick one from the *Choose a robot to simulate* prompt. This runs a quick gravity-and-joints sanity check, not a full engineering simulation.
- **Assembly** — the parts and joints list for the selected item, plus a **Configure export** option. Select a robot item to see its links and joints, or build a new assembly from your existing items.

## Inspecting a selected item

Selecting anything (in the graph, the explorer list, or the assembly tree) opens its details on the right:

- **Overview** — status, type, and properties. Use the **File** section to **Download** the stored file, **Open** it in a new tab, or **Preview** it full-screen. Sketches show a *Needs approval* label until approved.
- **View 3D Model** (design items only) — loads the item into the 3D view. The scissors button cuts one shape out of another: pick a second item as the cutter, then choose **Hole** or **Group**. The factory button opens **Export for robotics sim**, where you pick a format (URDF, SDF, or USD), a material, and names — then download the resulting files.
- **View Robot** (robot items only) — loads the saved robot straight into the 3D view, no form needed.
- **Properties** — the stored details for the item.
- **History** — past revisions with descriptions and times. Expands to show all.
- **Proposals** — suggested changes for this item; approve or reject them here instead of going to Approvals.

## Adding 3D files

Select the upload icon in the header (**Import work product**): choose a **quality** (Preview, Standard, or Fine), then **Choose STEP or IGES file**. The button shows progress as *uploading → converting → loading*. If it fails you will see *Import failed* — check your connection and file type. Imported items also appear under Files.

## Asking questions about an item

The conversation panel (which you can minimise or expand) is scoped to your project and, when an item is selected, to that item. Ask things like "why is this flagged?" or "summarise the history of this part". Approving a suggestion from the conversation updates the 3D view.

## Try the sample workspace (no connection needed)

The top bar's **Sample** link opens an example drone project that runs entirely in your browser with no connection. It is labelled *Sample data · resets on refresh* — a safe place to learn the layout (explorer, four views, details, conversation) before working on real projects.
