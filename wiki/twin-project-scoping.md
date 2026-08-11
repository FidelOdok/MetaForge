# Twin Project Scoping

A work product's project membership lives in **two independent places**, read by two different views:

1. **Postgres junction** (`link_work_product_to_project`) — read by `project_get` and the dashboard **Projects page**.
2. **The twin node's `project_id` attribute** (on `NodeBase`) — read by the **`/twin` page**, whose backend `list_work_products(project_id=...)` filters by attribute equality on `project_id`, not a graph relationship.

Writing only one makes a node appear in one view but not the other, and "All projects" in the `/twin` view will still show it (it skips the filter), which can mask the bug during casual testing.

**Rule**: any code path that creates a project-owned node must set `project_id=<uuid>` on the `WorkProduct`/node constructor **and** create the Postgres junction link. Both, every time — there is no single call that does both for you unless you're going through an existing recorder (`record_decision`, `geometry_recorder`) that already handles it correctly.

**Symptom to recognize**: a node visible on the Projects page but invisible when filtering the Twin view to that specific project (not "All projects") almost always means only the junction was written.

Related: [CAD/FEA Adapter Containers](cad-fea-adapter-containers.md), [Digital Twin](digital-twin.md).
