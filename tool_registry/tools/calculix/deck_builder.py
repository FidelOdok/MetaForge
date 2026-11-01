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


def _fmt(value: float) -> str:
    """Format a float for a CalculiX free-format numeric card.

    Plain Python interpolation (``f"{value}"``) uses ``repr()``, which can
    emit up to 17 significant digits for a value that doesn't round-trip
    exactly in binary -- e.g. ``7850 * 1e-12`` prints as
    ``'7.849999999999999e-09'``. Confirmed live on fidel-dev: CalculiX's
    Fortran free-format reader silently misparses that exact string in a
    ``*DENSITY`` card, understating the assembled mass by a factor of
    ~2.6e8 with NO error or warning -- reporting a modal deck's first
    natural frequency as 0.05 Hz instead of the correct ~815 Hz (confirmed
    by re-solving the identical deck with only that one string swapped for
    a shorter ``7.85E-09``). Bounding to 10 significant digits keeps every
    value this codebase's own materials/mesh data can produce well within
    CalculiX's reader while staying far more precise than the parts models
    this feeds are ever measured to.
    """
    return f"{value:.10G}"


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
        lines.append(f"{node_id}, {_fmt(x)}, {_fmt(y)}, {_fmt(z)}")

    for etype, element_ids in volume_by_type.items():
        lines.append(f"*ELEMENT, TYPE={etype}, ELSET={volume_elset}")
        for element_id in element_ids:
            _etype, node_ids = mesh.elements[element_id]
            lines.append(f"{element_id}, " + ", ".join(str(n) for n in node_ids))

    lines.append(f"*NSET, NSET={fixed_nset}")
    lines.extend(_format_id_list(fixed_nodes))

    lines.append("*MATERIAL, NAME=MAT1")
    lines.append("*ELASTIC, TYPE=ISO")
    lines.append(f"{_fmt(youngs_modulus_mpa)}, {_fmt(poissons_ratio)}")
    lines.append(f"*SOLID SECTION, ELSET={volume_elset}, MATERIAL=MAT1")
    lines.append("*STEP")
    lines.append("*STATIC")
    lines.append("*BOUNDARY")
    lines.append(f"{fixed_nset}, 1, 3")
    lines.append("*CLOAD")
    for node_id in load_nodes:
        if fx_per_node:
            lines.append(f"{node_id}, 1, {_fmt(fx_per_node)}")
        if fy_per_node:
            lines.append(f"{node_id}, 2, {_fmt(fy_per_node)}")
        if fz_per_node:
            lines.append(f"{node_id}, 3, {_fmt(fz_per_node)}")
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*EL FILE")
    lines.append("S")
    lines.append("*END STEP")

    return "\n".join(lines) + "\n"


def build_thermal_deck(
    mesh: MeshData,
    *,
    conductivity_w_mm_k: float,
    heat_source_node_set: str,
    power_dissipation_w: float,
    sink_node_set: str,
    sink_temp_c: float,
    volume_elset: str = "Volume1",
) -> str:
    """Return a complete, solvable CalculiX steady-state conduction deck
    (FORGE-282, gap G-F6) -- same "real cards around the mesh's volume
    elements only" shape as :func:`build_static_stress_deck`/
    :func:`build_modal_deck`, but a ``*HEAT TRANSFER, STEADY STATE`` step:
    ``*CONDUCTIVITY`` instead of ``*ELASTIC``/``*DENSITY``, a ``*CFLUX``
    heat source (the thermal sibling of ``*CLOAD``, DOF 11 = temperature)
    instead of ``*CLOAD``, and a fixed-temperature ``*BOUNDARY`` (DOF 11)
    instead of a fixed-displacement one (DOF 1-3).

    This is deliberately a CONDUCTION-only model: ``sink_node_set`` is held
    at a fixed ``sink_temp_c`` (e.g. a bracket bolted to a chassis/heatsink
    held near-ambient by a much larger thermal mass), and heat flows from
    ``heat_source_node_set`` to it purely by conduction through the part.
    A convective (``*FILM``) boundary condition to open air is explicitly
    NOT built here -- CalculiX's ``*FILM`` card is specified per element
    FACE (or a ``*SURFACE`` group of faces), and nothing in this mesh-
    handling codebase derives exposed element faces from a mesh today (every
    existing deck builder only ever references node sets from gmsh's own
    per-STEP-face named elsets, e.g. ``Surface1``) -- deriving real exposed-
    face geometry is a separate, more novel piece of work than this
    function's scope. A fixed-temperature sink is a real, well-precedented
    simplification (the same "one well-understood case" discipline
    :func:`build_static_stress_deck`'s own module docstring describes), not
    a stand-in for a convective model.

    ``conductivity_w_mm_k`` -- CalculiX has no built-in unit system, and the
    mm+N+MPa consistent triple the static/modal decks use extends naturally
    to a length-cubed-free thermal quantity: conductivity's SI unit
    W/(m*K) becomes W/(mm*K) by multiplying by 1e-3 (1/m = 1e-3/mm). Getting
    this wrong doesn't fail loudly -- it just reports a confidently wrong
    peak temperature, off by exactly 1e3x (a length-unit error, not
    length-cubed like the modal deck's density conversion, since
    conductivity is a "per length" quantity, not "per volume"). Callers
    should use :func:`tool_registry.tools.cadquery.materials.
    resolve_thermal_conductivity_w_mk` and convert once at the call site,
    not duplicate the constant.

    ``power_dissipation_w`` -- a TOTAL heat generation (Watts) distributed
    EVENLY across every node in ``heat_source_node_set`` (one ``*CFLUX``
    line per node, each already divided by the node count) -- same
    total-divided-per-node discipline as ``load_force_n`` in
    :func:`build_static_stress_deck` (applying it via the node SET name
    directly would multiply the effective total power by the node count).
    Watts needs no unit-system conversion here -- like Newtons in the
    static deck, it's an extensive SI base-compatible quantity independent
    of the mesh's chosen length unit.

    Same argument-validation contract as :func:`build_static_stress_deck`
    (unknown volume_elset/node sets, empty node sets, no real volume
    elements) -- see that function's docstring.
    """
    if volume_elset not in mesh.elsets:
        raise ValueError(
            f"build_thermal_deck: no element set {volume_elset!r} in this mesh -- "
            f"available element sets: {sorted(mesh.elsets)}"
        )
    heat_source_nodes = mesh.node_ids_for_elset(heat_source_node_set)
    sink_nodes = mesh.node_ids_for_elset(sink_node_set)
    if not heat_source_nodes:
        raise ValueError(f"build_thermal_deck: {heat_source_node_set!r} has no nodes")
    if not sink_nodes:
        raise ValueError(f"build_thermal_deck: {sink_node_set!r} has no nodes")

    volume_by_type: dict[str, list[int]] = {}
    for element_id in mesh.elsets[volume_elset]:
        etype, _node_ids = mesh.elements[element_id]
        if etype.startswith(_VOLUME_ELEMENT_PREFIXES):
            volume_by_type.setdefault(etype, []).append(element_id)
    if not volume_by_type:
        raise ValueError(
            f"build_thermal_deck: element set {volume_elset!r} has no volume (C3D*) "
            f"elements -- nothing for a thermal solve to act on"
        )
    kept_node_ids: set[int] = set()
    for element_ids in volume_by_type.values():
        for element_id in element_ids:
            kept_node_ids.update(mesh.elements[element_id][1])
    missing = (set(heat_source_nodes) | set(sink_nodes)) - kept_node_ids
    if missing:
        raise ValueError(
            f"build_thermal_deck: {len(missing)} node(s) in "
            f"{heat_source_node_set!r}/{sink_node_set!r} aren't part of any kept "
            f"volume element -- e.g. {sorted(missing)[:5]} -- the mesh may be "
            "inconsistent"
        )

    sink_nset = f"SINK_{sink_node_set}"
    n = len(heat_source_nodes)
    power_per_node = power_dissipation_w / n

    lines: list[str] = ["*Heading", " MetaForge FORGE-282 steady-state thermal deck", "*NODE"]
    for node_id in sorted(kept_node_ids):
        x, y, z = mesh.nodes[node_id]
        lines.append(f"{node_id}, {_fmt(x)}, {_fmt(y)}, {_fmt(z)}")

    for etype, element_ids in volume_by_type.items():
        lines.append(f"*ELEMENT, TYPE={etype}, ELSET={volume_elset}")
        for element_id in element_ids:
            _etype, node_ids = mesh.elements[element_id]
            lines.append(f"{element_id}, " + ", ".join(str(n) for n in node_ids))

    lines.append(f"*NSET, NSET={sink_nset}")
    lines.extend(_format_id_list(sink_nodes))

    lines.append("*MATERIAL, NAME=MAT1")
    lines.append("*CONDUCTIVITY")
    lines.append(_fmt(conductivity_w_mm_k))
    lines.append(f"*SOLID SECTION, ELSET={volume_elset}, MATERIAL=MAT1")
    lines.append("*STEP")
    lines.append("*HEAT TRANSFER, STEADY STATE")
    lines.append("*BOUNDARY")
    lines.append(f"{sink_nset}, 11, 11, {_fmt(sink_temp_c)}")
    lines.append("*CFLUX")
    for node_id in heat_source_nodes:
        lines.append(f"{node_id}, 11, {_fmt(power_per_node)}")
    lines.append("*NODE FILE")
    lines.append("NT")
    lines.append("*END STEP")

    return "\n".join(lines) + "\n"


def build_modal_deck(
    mesh: MeshData,
    *,
    youngs_modulus_mpa: float,
    poissons_ratio: float,
    density_tonne_mm3: float,
    fixed_node_set: str,
    num_modes: int,
    volume_elset: str = "Volume1",
) -> str:
    """Return a complete, solvable CalculiX modal (natural-frequency) deck
    (FORGE-281, gap G-F5) -- same "real cards around the mesh's volume
    elements only" shape as :func:`build_static_stress_deck`, sharing its
    node/element/nset-building logic, but a ``*FREQUENCY`` step instead of
    ``*STATIC``: no load (an eigenvalue problem has none) and a ``*DENSITY``
    card the static-stress deck never needs (a mass matrix requires mass;
    a pure stiffness solve doesn't).

    ``density_tonne_mm3`` -- CalculiX has no built-in unit system, and the
    mm+N+MPa consistent triple :func:`build_static_stress_deck` already uses
    forces a mass unit of the TONNE, not the kilogram: F=ma with F in N and a
    in mm/s^2 makes the mass unit N*s^2/mm, which works out to 1000 kg (see
    module docstring's own mm/N/MPa consistency note, extended one unit
    further for a dynamics problem). A material's density in the ordinary
    kg/m^3 tables (``tool_registry.tools.cadquery.materials.
    MATERIAL_DENSITY_KG_M3``) must be multiplied by 1e-12 before it belongs
    in this deck -- getting this wrong doesn't fail loudly, it just reports
    confidently wrong frequencies (a mass-unit error under a square root, so
    off by exactly sqrt(1e12) = 1e6x). Callers should use
    :func:`resolve_density_kg_m3` and convert once at the call site, not
    duplicate the constant.

    ``num_modes`` is the number of natural frequencies/mode shapes to
    extract, lowest first (CalculiX's own Lanczos default).

    Same argument-validation contract as :func:`build_static_stress_deck`
    (unknown volume_elset/fixed_node_set, empty node sets, no real volume
    elements) -- see that function's docstring.
    """
    if volume_elset not in mesh.elsets:
        raise ValueError(
            f"build_modal_deck: no element set {volume_elset!r} in this mesh -- "
            f"available element sets: {sorted(mesh.elsets)}"
        )
    fixed_nodes = mesh.node_ids_for_elset(fixed_node_set)
    if not fixed_nodes:
        raise ValueError(f"build_modal_deck: {fixed_node_set!r} has no nodes")
    if num_modes < 1:
        raise ValueError(f"build_modal_deck: num_modes must be >= 1, got {num_modes}")

    volume_by_type: dict[str, list[int]] = {}
    for element_id in mesh.elsets[volume_elset]:
        etype, _node_ids = mesh.elements[element_id]
        if etype.startswith(_VOLUME_ELEMENT_PREFIXES):
            volume_by_type.setdefault(etype, []).append(element_id)
    if not volume_by_type:
        raise ValueError(
            f"build_modal_deck: element set {volume_elset!r} has no volume (C3D*) "
            f"elements -- nothing for a modal solve to act on"
        )
    kept_node_ids: set[int] = set()
    for element_ids in volume_by_type.values():
        for element_id in element_ids:
            kept_node_ids.update(mesh.elements[element_id][1])
    missing = set(fixed_nodes) - kept_node_ids
    if missing:
        raise ValueError(
            f"build_modal_deck: {len(missing)} node(s) in {fixed_node_set!r} aren't "
            f"part of any kept volume element -- e.g. {sorted(missing)[:5]} -- the "
            f"mesh may be inconsistent"
        )

    fixed_nset = f"FIXED_{fixed_node_set}"

    lines: list[str] = ["*Heading", " MetaForge FORGE-281 modal (natural frequency) deck", "*NODE"]
    for node_id in sorted(kept_node_ids):
        x, y, z = mesh.nodes[node_id]
        lines.append(f"{node_id}, {_fmt(x)}, {_fmt(y)}, {_fmt(z)}")

    for etype, element_ids in volume_by_type.items():
        lines.append(f"*ELEMENT, TYPE={etype}, ELSET={volume_elset}")
        for element_id in element_ids:
            _etype, node_ids = mesh.elements[element_id]
            lines.append(f"{element_id}, " + ", ".join(str(n) for n in node_ids))

    lines.append(f"*NSET, NSET={fixed_nset}")
    lines.extend(_format_id_list(fixed_nodes))

    lines.append("*MATERIAL, NAME=MAT1")
    lines.append("*ELASTIC, TYPE=ISO")
    lines.append(f"{_fmt(youngs_modulus_mpa)}, {_fmt(poissons_ratio)}")
    lines.append("*DENSITY")
    lines.append(_fmt(density_tonne_mm3))
    lines.append(f"*SOLID SECTION, ELSET={volume_elset}, MATERIAL=MAT1")
    lines.append("*STEP")
    lines.append("*FREQUENCY")
    lines.append(f"{num_modes}")
    lines.append("*BOUNDARY")
    lines.append(f"{fixed_nset}, 1, 3")
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*END STEP")

    return "\n".join(lines) + "\n"
