# define_system_architecture

Persists a component/interface map as a `SYSTEM_ARCHITECTURE` work product
via `twin.commit_system_architecture` -- captures what talks to what before
detailed design starts. Cross-discipline (not owned by one specialist agent),
so it lives in `shared`, not a single domain.

## What it does

1. Takes named components (with a discipline tag) and interfaces between
   them (from, to, type).
2. Calls `twin.commit_system_architecture`, which renders a mermaid block
   diagram + component/interface tables and flags any interface whose
   `from`/`to` doesn't match a declared component.

## Input

`project_id`, `system_name`, `components` (name, discipline, description),
`interfaces` (from, to, interface_type, description).

## Output

`node_id`, `component_count`, `interface_count`, `dangling_interface_count`
(non-zero means a real gap -- an interface references something not in the
component list).

## Limitations

Flags dangling interfaces but does not block on them -- the architecture is
still persisted for review even if incomplete.
