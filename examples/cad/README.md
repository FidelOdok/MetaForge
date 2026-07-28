# CAD assembly examples

Declarative multi-part assembly specs for `forge cad build` (see
[`docs/cli-reference.md`](../../docs/cli-reference.md#cad-build--author-a-multi-part-assembly)).
Each spec authors a real FreeCAD assembly deterministically (no LLM) and commits
it to the twin as a loadable `cad_model`.

## Pan-Tilt Camera Gimbal (2-DOF)

A worked reference product decomposed into three sub-assemblies:

| Spec | Sub-assembly | Parts |
|------|--------------|-------|
| `gimbal-base.json` | Base | base plate, yaw motor housing, tripod boss |
| `gimbal-yaw.json` | Yaw stage | yaw bearing ring, yaw arm |
| `gimbal-pitch.json` | Pitch stage | pitch motor mount, pitch shaft, camera cradle |

Build the whole gimbal into one project:

```bash
PID=$(python -m cli.forge_cli projects list --json | jq -r '.projects[] | select(.name=="Pan-Tilt Camera Gimbal") | .id')
for spec in gimbal-base gimbal-yaw gimbal-pitch; do
  python -m cli.forge_cli cad build "examples/cad/$spec.json" --project-id "$PID"
done
```

Each committed assembly is loadable in the 3D viewer and shows on the project's
Projects page. The same assemblies can also be authored conversationally with
`forge chat` (the agent drives the FreeCAD tools and commits by reference).
