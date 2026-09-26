"""Builds a complete, solvable CalculiX static-stress analysis deck around
a mesh-only ``.inp`` file (FORGE-234).

Before this, ``calculix.run_fea`` invoked ``ccx`` directly against
``freecad.generate_mesh``'s own output -- nodes and elements only, no
``*MATERIAL``/``*SOLID SECTION``/``*STEP``/``*STATIC``/``*BOUNDARY``/
``*CLOAD``, no ``*NODE FILE``/``*EL FILE`` output request. There was
nothing for CalculiX to actually solve, so it exited 0 having solved
nothing (FORGE-232 made that fail loudly instead of silently; this module
is the other half -- giving it something real to solve).

Units: every FreeCAD/gmsh-generated mesh's node coordinates are in
millimeters. CalculiX has no built-in unit system -- it is only ever as
consistent as the numbers fed into it. Length=mm + Force=N + Stress=MPa
(== N/mm^2) is the standard consistent triple for mm-scale geometry; Young's
modulus MUST be given in MPa here, not Pa (see
``tool_registry.tools.cadquery.materials.MATERIAL_ELASTIC_MPA``) -- Pa with
mm-scale geometry would understate stiffness by 1e6, six orders of
magnitude, not a rounding error.
"""

from __future__ import annotations

from tool_registry.tools.calculix.inp_mesh import MeshData

_IDS_PER_LINE = 10

# gmsh's default STEP-to-tetrahedra output (freecad.generate_mesh) always
# includes lower-dimensional CPS3 (surface triangle)/T3D2 (edge) elements
# alongside the real C3D4 volume tets -- confirmed live: leaving them in a
# solved deck makes ccx fail ("gen3delem: first thickness ... is zero"),
# because every element ccx reads needs a section, and a shell/beam section
# for elements that only exist here as free face/edge node-set markers
# would be actively wrong (they're not meant to carry their own stiffness --
# they'd double up shell/beam stiffness contributions on top of the volume
# mesh they lie on). The mesh's own type prefix distinguishes them, matching
# operations.py's identical convention for the same reason.
_VOLUME_ELEMENT_PREFIXES = ("C3D",)


def _format_id_list(ids: list[int]) -> list[str]:
    """CalculiX/Abaqus data block: comma-separated ids, wrapped at a
    reasonable line width -- the reader just consumes values across lines
    until the next ``*`` card, no continuation marker needed."""
    return [
        ", ".join(str(i) for i in ids[start : start + _IDS_PER_LINE])
        for start in range(0, len(ids), _IDS_PER_LINE)
    ]


def build_static_stress_deck(
    mesh: MeshData,
    *,
    youngs_modulus_mpa: float,
    poissons_ratio: float,
    fixed_node_set: str,
    load_node_set: str,
    load_force_n: tuple[float, float, float],
    volume_elset: str = "Volume1",
) -> str:
    """Return a complete, from-scratch CalculiX deck: only the solid
    (volume) elements the mesh actually needs solved, plus real analysis
    cards -- NOT the original mesh file with cards appended (that still
    contains the CPS3/T3D2 elements that make ccx fail; see module docstring).

    ``fixed_node_set``/``load_node_set`` name existing element sets in the
    mesh (e.g. ``"Surface1"``, gmsh's own per-STEP-face groups) -- their
    node sets are derived here (``MeshData.node_ids_for_elset``), not
    assumed to already exist as ``*NSET``s in the source file.

    ``load_force_n`` is a TOTAL force (Fx, Fy, Fz) in Newtons, distributed
    EVENLY across every node in ``load_node_set`` (one ``*CLOAD`` line per
    node, per nonzero component, each already divided by the node count) --
    a reasonable, well-understood approximation for a structural sanity
    check, not a traction-consistent (shape-function-weighted) load
    application. Applying it via the node SET name directly in ``*CLOAD``
    instead (Abaqus/CalculiX's shorthand for "this same magnitude at every
    member") would multiply the effective total force by the node count --
    a correctness bug this deliberately avoids by emitting individual node
    lines with pre-divided magnitudes.

    Raises ``ValueError``/``KeyError`` for any referenced elset/node set
    that doesn't exist, is empty, or (``volume_elset``) contains no real
    volume (``C3D*``) elements -- never silently builds a deck that solves
    the wrong (or no) boundary conditions.
    """
    if volume_elset not in mesh.elsets:
        raise ValueError(
            f"build_static_stress_deck: no element set {volume_elset!r} in this "
            f"mesh -- available element sets: {sorted(mesh.elsets)}"
        )
    fixed_nodes = mesh.node_ids_for_elset(fixed_node_set)
    load_nodes = mesh.node_ids_for_elset(load_node_set)
    if not fixed_nodes:
        raise ValueError(f"build_static_stress_deck: {fixed_node_set!r} has no nodes")
    if not load_nodes:
        raise ValueError(f"build_static_stress_deck: {load_node_set!r} has no nodes")

    # Only the real volume elements -- see _VOLUME_ELEMENT_PREFIXES above.
    volume_by_type: dict[str, list[int]] = {}
    for element_id in mesh.elsets[volume_elset]:
        etype, _node_ids = mesh.elements[element_id]
        if etype.startswith(_VOLUME_ELEMENT_PREFIXES):
            volume_by_type.setdefault(etype, []).append(element_id)
    if not volume_by_type:
        raise ValueError(
            f"build_static_stress_deck: element set {volume_elset!r} has no volume "
            f"(C3D*) elements -- nothing for a structural solve to act on"
        )
    # Every node referenced by a kept volume element -- the *NODE block only
    # needs to define these (fixed/load node sets are always a subset, since
    # a mesh's surface nodes are always shared with an adjacent volume
    # element in a consistent tessellation).
    kept_node_ids: set[int] = set()
    for element_ids in volume_by_type.values():
        for element_id in element_ids:
            kept_node_ids.update(mesh.elements[element_id][1])
    missing = (set(fixed_nodes) | set(load_nodes)) - kept_node_ids
    if missing:
        raise ValueError(
            f"build_static_stress_deck: {len(missing)} node(s) in "
            f"{fixed_node_set!r}/{load_node_set!r} aren't part of any kept volume "
            f"element -- e.g. {sorted(missing)[:5]} -- the mesh may be inconsistent"
        )

    fixed_nset = f"FIXED_{fixed_node_set}"
    fx, fy, fz = load_force_n
    n = len(load_nodes)
    fx_per_node, fy_per_node, fz_per_node = fx / n, fy / n, fz / n

    lines: list[str] = ["*Heading", " MetaForge FORGE-234 static stress deck", "*NODE"]
    for node_id in sorted(kept_node_ids):
        x, y, z = mesh.nodes[node_id]
        lines.append(f"{node_id}, {x}, {y}, {z}")

    for etype, element_ids in volume_by_type.items():
        lines.append(f"*ELEMENT, TYPE={etype}, ELSET={volume_elset}")
        for element_id in element_ids:
            _etype, node_ids = mesh.elements[element_id]
            lines.append(f"{element_id}, " + ", ".join(str(n) for n in node_ids))

    lines.append(f"*NSET, NSET={fixed_nset}")
    lines.extend(_format_id_list(fixed_nodes))

    lines.append("*MATERIAL, NAME=MAT1")
    lines.append("*ELASTIC, TYPE=ISO")
    lines.append(f"{youngs_modulus_mpa}, {poissons_ratio}")
    lines.append(f"*SOLID SECTION, ELSET={volume_elset}, MATERIAL=MAT1")
    lines.append("*STEP")
    lines.append("*STATIC")
    lines.append("*BOUNDARY")
    lines.append(f"{fixed_nset}, 1, 3")
    lines.append("*CLOAD")
    for node_id in load_nodes:
        if fx_per_node:
            lines.append(f"{node_id}, 1, {fx_per_node}")
        if fy_per_node:
            lines.append(f"{node_id}, 2, {fy_per_node}")
        if fz_per_node:
            lines.append(f"{node_id}, 3, {fz_per_node}")
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*EL FILE")
    lines.append("S")
    lines.append("*END STEP")

    return "\n".join(lines) + "\n"
