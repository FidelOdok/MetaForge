"""CalculiX input-deck generation.

A mesher emits geometry: nodes, elements, maybe a named set or two. CalculiX
needs considerably more before it will produce a result -- a material, a section
binding that material to elements, boundary conditions, loads, an analysis step,
and explicit output requests. Without those cards the solver either refuses the
job or exits zero having written an empty ``.frd``.

This module turns a parsed :class:`~tool_registry.tools.calculix.mesh.Mesh` plus
a load case into a complete, solvable deck. The mesh itself is pulled in with
``*INCLUDE`` rather than copied, so a million-element mesh does not have to be
re-serialised for every load case in a sweep.

All quantities follow the N-mm-s-tonne-K consistent system documented in
:mod:`tool_registry.tools.calculix.materials`: forces in N, stresses in MPa,
lengths in mm, temperatures in K.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, model_validator

from tool_registry.tools.calculix.materials import Material
from tool_registry.tools.calculix.mesh import Mesh, select_nodes_on_face

logger = structlog.get_logger(__name__)

#: Prefix for sets this module generates, so they cannot collide with sets the
#: mesher already emitted.
SET_PREFIX = "MF"

#: Degrees of freedom implied by the named constraint kinds.
CONSTRAINT_KINDS: dict[str, tuple[int, ...]] = {
    "fixed": (1, 2, 3),
    "encastre": (1, 2, 3),
    "pinned": (1, 2, 3),
    "roller_x": (1,),
    "roller_y": (2,),
    "roller_z": (3,),
    "symmetry_x": (1,),
    "symmetry_y": (2,),
    "symmetry_z": (3,),
}

#: CalculiX degree of freedom used for temperature in a heat transfer step.
TEMPERATURE_DOF = 11


class DeckError(ValueError):
    """Raised when a load case cannot be turned into a solvable deck."""


class Region(BaseModel):
    """A named or geometric selection of mesh nodes.

    Exactly one of ``node_set`` or ``face`` identifies the region. ``node_set``
    names a set the mesher emitted; ``face`` picks a bounding-box face
    geometrically, which is what most load cases need because meshers rarely
    emit named face sets.
    """

    node_set: str | None = Field(default=None, description="Name of an existing *NSET")
    face: str | None = Field(
        default=None,
        description="Bounding-box face: xmin, xmax, ymin, ymax, zmin, zmax",
    )
    tolerance: float | None = Field(
        default=None,
        gt=0,
        description="Face selection tolerance in mm; defaults to 0.1% of the model diagonal",
    )

    @model_validator(mode="after")
    def _require_one_selector(self) -> Region:
        """Reject a region that names neither or both selectors."""
        if bool(self.node_set) == bool(self.face):
            raise ValueError("A region must specify exactly one of 'node_set' or 'face'")
        return self

    def resolve(self, mesh: Mesh) -> list[int]:
        """Resolve this region to node ids.

        Raises:
            DeckError: If the named set does not exist or the selection is empty.
                An empty region means the load or constraint would silently do
                nothing, which is worse than failing.
        """
        if self.node_set:
            try:
                nodes = mesh.resolve_node_set(self.node_set)
            except KeyError:
                available = ", ".join(sorted(mesh.node_sets)) or "none"
                raise DeckError(
                    f"Node set '{self.node_set}' not found in mesh. Available sets: {available}"
                ) from None
        else:
            assert self.face is not None  # guaranteed by _require_one_selector
            try:
                nodes = select_nodes_on_face(mesh, self.face, self.tolerance)
            except ValueError as exc:
                raise DeckError(str(exc)) from exc

        if not nodes:
            raise DeckError(
                f"Region {self.describe()} selected no nodes -- "
                "the load or constraint would have no effect"
            )
        return sorted(set(nodes))

    def describe(self) -> str:
        """Short human-readable label for logs and error messages."""
        return f"node_set={self.node_set}" if self.node_set else f"face={self.face}"


class Constraint(BaseModel):
    """A displacement boundary condition."""

    region: Region = Field(..., description="Nodes to constrain")
    kind: str = Field(default="fixed", description=f"One of: {', '.join(CONSTRAINT_KINDS)}")
    dofs: list[int] | None = Field(
        default=None,
        description="Explicit degrees of freedom 1-3; overrides 'kind' when given",
    )
    value: float = Field(default=0.0, description="Prescribed displacement in mm")

    def resolved_dofs(self) -> tuple[int, ...]:
        """Degrees of freedom this constraint fixes.

        Raises:
            DeckError: If ``kind`` is unknown or an explicit dof is out of range.
        """
        if self.dofs:
            for dof in self.dofs:
                if dof not in (1, 2, 3):
                    raise DeckError(f"Displacement degree of freedom must be 1, 2 or 3, got {dof}")
            return tuple(self.dofs)

        kind = self.kind.strip().lower()
        if kind not in CONSTRAINT_KINDS:
            raise DeckError(
                f"Unknown constraint kind '{self.kind}'. "
                f"Known kinds: {', '.join(sorted(CONSTRAINT_KINDS))}"
            )
        return CONSTRAINT_KINDS[kind]


class PointLoad(BaseModel):
    """A concentrated force applied to a region."""

    region: Region = Field(..., description="Nodes to load")
    fx: float = Field(default=0.0, description="Force along X, in N")
    fy: float = Field(default=0.0, description="Force along Y, in N")
    fz: float = Field(default=0.0, description="Force along Z, in N")
    distribute: bool = Field(
        default=True,
        description=(
            "Split the force equally across the region's nodes (a total force). "
            "When False the full force is applied at every node."
        ),
    )

    def components(self) -> list[tuple[int, float]]:
        """Non-zero ``(dof, magnitude)`` pairs for this load."""
        return [(dof, v) for dof, v in ((1, self.fx), (2, self.fy), (3, self.fz)) if v != 0.0]


class Pressure(BaseModel):
    """A distributed pressure on an element face."""

    element_set: str = Field(..., min_length=1, description="Name of an existing *ELSET")
    face_label: str = Field(
        default="P1",
        description="CalculiX element face label (P1..P6) the pressure acts on",
    )
    magnitude_mpa: float = Field(..., description="Pressure in MPa; positive acts inward")


class ThermalBoundary(BaseModel):
    """A prescribed temperature on a region."""

    region: Region = Field(..., description="Nodes held at a fixed temperature")
    temperature_k: float = Field(..., description="Prescribed temperature in K")


class HeatFlux(BaseModel):
    """A concentrated heat flux into a region."""

    region: Region = Field(..., description="Nodes receiving the flux")
    power_mw: float = Field(
        ...,
        description="Total heat input in mW (N*mm/s). 1 W = 1000 mW.",
    )
    distribute: bool = Field(
        default=True, description="Split the power equally across the region's nodes"
    )


class Convection(BaseModel):
    """Convective film cooling on an element face."""

    element_set: str = Field(..., min_length=1, description="Name of an existing *ELSET")
    face_label: str = Field(default="F1", description="CalculiX face label (F1..F6)")
    film_coefficient: float = Field(
        ...,
        gt=0,
        description="Heat transfer coefficient in mW/(mm^2*K); 1 W/(m^2*K) = 1e-3 mW/(mm^2*K)",
    )
    sink_temperature_k: float = Field(..., description="Ambient/sink temperature in K")


class LoadCase(BaseModel):
    """One loading scenario to solve.

    A load case must restrain the model. An unrestrained static solve has rigid
    body modes, and CalculiX reports that as a singular stiffness matrix rather
    than a stress result -- so an empty constraint list is rejected up front,
    where the error can name the load case.
    """

    name: str = Field(default="load_case", min_length=1, description="Identifier for the case")
    constraints: list[Constraint] = Field(default_factory=list)
    point_loads: list[PointLoad] = Field(default_factory=list)
    pressures: list[Pressure] = Field(default_factory=list)
    gravity_mm_s2: list[float] | None = Field(
        default=None,
        description="Body acceleration vector [gx, gy, gz] in mm/s^2; earth gravity is 9810",
    )
    thermal_boundaries: list[ThermalBoundary] = Field(default_factory=list)
    heat_fluxes: list[HeatFlux] = Field(default_factory=list)
    convections: list[Convection] = Field(default_factory=list)

    def has_mechanical_load(self) -> bool:
        """Whether this case applies any mechanical load."""
        return bool(self.point_loads or self.pressures or self.gravity_mm_s2)

    def has_thermal_load(self) -> bool:
        """Whether this case applies any thermal boundary condition."""
        return bool(self.thermal_boundaries or self.heat_fluxes or self.convections)


class StepOptions(BaseModel):
    """Analysis-step settings."""

    analysis: Literal["static", "modal", "thermal"] = Field(default="static")
    nlgeom: bool = Field(
        default=False, description="Include geometric nonlinearity (large deformation)"
    )
    eigenmodes: int = Field(default=10, ge=1, le=200, description="Modes to extract (modal only)")
    steady_state: bool = Field(default=True, description="Steady-state thermal (thermal only)")
    time_increment: float = Field(default=1.0, gt=0, description="Initial increment, in s")
    time_period: float = Field(default=1.0, gt=0, description="Total step time, in s")
    initial_temperature_k: float = Field(
        default=293.15, description="Initial temperature for a thermal step, in K"
    )


def _format_number(value: float) -> str:
    """Render a float in a form CalculiX parses without precision loss."""
    if value == 0.0:
        return "0.0"
    if abs(value) < 1e-4 or abs(value) >= 1e6:
        return f"{value:.10E}"
    return f"{value:.10g}"


def _format_id_block(ids: list[int], per_line: int = 8) -> list[str]:
    """Wrap a list of ids into comma-separated lines CalculiX accepts.

    CalculiX limits a data line to 16 fields; 8 keeps the deck readable and
    leaves headroom.
    """
    lines: list[str] = []
    for start in range(0, len(ids), per_line):
        chunk = ids[start : start + per_line]
        line = ", ".join(str(i) for i in chunk)
        if start + per_line < len(ids):
            line += ","
        lines.append(line)
    return lines


class DeckBuilder:
    """Assembles a CalculiX input deck for one load case.

    The builder materialises every region into an explicit ``*NSET`` so the
    generated deck is self-describing: an engineer reviewing it can see exactly
    which nodes were constrained and loaded, rather than having to re-derive a
    geometric selection.
    """

    def __init__(
        self,
        mesh: Mesh,
        material: Material,
        mesh_include: str | None = None,
    ) -> None:
        """Initialise the builder.

        Args:
            mesh: The parsed mesh the deck refers to.
            material: Resolved material properties.
            mesh_include: Path used in the ``*INCLUDE`` card. Defaults to the
                mesh file's own name, which resolves when the deck is written
                beside it.
        """
        self.mesh = mesh
        self.material = material
        self.mesh_include = mesh_include or Path(mesh.source).name
        self._lines: list[str] = []
        self._set_lines: list[str] = []
        self._set_counter = 0
        self._generated_sets: dict[str, list[int]] = {}
        self._solid_elsets: list[str] | None = None

    # -- deck assembly -------------------------------------------------

    def _emit(self, *lines: str) -> None:
        """Append raw lines to the deck."""
        self._lines.extend(lines)

    def _emit_node_set(self, nodes: list[int], label: str) -> str:
        """Declare a ``*NSET`` for the given nodes and return its name.

        The card goes into the model-definition buffer, not wherever the deck is
        currently being written. CalculiX only accepts set definitions before the
        first ``*STEP``, so a set resolved while emitting a load must still be
        declared above the step that uses it.
        """
        self._set_counter += 1
        name = f"{SET_PREFIX}_{label}_{self._set_counter}".upper()
        self._generated_sets[name] = nodes
        self._set_lines.append(f"*NSET, NSET={name}")
        self._set_lines.extend(_format_id_block(nodes))
        return name

    def _region_set(self, region: Region, label: str) -> tuple[str, int]:
        """Resolve a region and emit it as a node set. Returns (name, count)."""
        nodes = region.resolve(self.mesh)
        return self._emit_node_set(nodes, label), len(nodes)

    def _emit_heading(self, load_case: LoadCase, options: StepOptions) -> None:
        """Write the deck header and pull in the mesh."""
        self._emit(
            "*HEADING",
            f"MetaForge {options.analysis} analysis -- case '{load_case.name}'",
            f"Material: {self.material.name} "
            f"(E={_format_number(self.material.youngs_modulus_mpa)} MPa, "
            f"nu={self.material.poissons_ratio})",
            "Units: N-mm-s-tonne-K (stresses in MPa)",
            "**",
            f"*INCLUDE, INPUT={self.mesh_include}",
            "**",
        )

    def _emit_material(self, thermal: bool) -> None:
        """Write the material definition and bind it to every solid element set."""
        material = self.material
        self._emit(
            "**",
            "** Material properties",
            f"*MATERIAL, NAME={material.name}",
            "*ELASTIC",
            f"{_format_number(material.youngs_modulus_mpa)}, "
            f"{_format_number(material.poissons_ratio)}",
            "*DENSITY",
            _format_number(material.density_tonne_mm3),
        )

        if thermal:
            self._emit(
                "*CONDUCTIVITY",
                _format_number(material.thermal_conductivity),
                "*SPECIFIC HEAT",
                _format_number(material.specific_heat),
                f"*EXPANSION, ZERO={_format_number(material.reference_temperature_k)}",
                _format_number(material.thermal_expansion_per_k),
            )

        for elset in self._solid_element_sets():
            self._emit(f"*SOLID SECTION, ELSET={elset}, MATERIAL={material.name}")

    def _solid_element_sets(self) -> list[str]:
        """Element sets to attach a solid section to.

        Prefers the sets the mesher emitted. If it emitted none -- or none that
        contain solid elements -- one is synthesised covering every solid
        element, because an element without a section has no stiffness and
        CalculiX will reject the job.
        """
        if self._solid_elsets is not None:
            return self._solid_elsets

        solid_ids = {e.eid for e in self.mesh.solid_elements}
        if not solid_ids:
            raise DeckError(
                "Mesh contains no solid (3D) elements -- "
                f"found element types: {', '.join(self.mesh.element_types) or 'none'}"
            )

        usable = [
            name
            for name, ids in self.mesh.element_sets.items()
            if any(eid in solid_ids for eid in ids)
        ]
        if usable:
            self._solid_elsets = sorted(usable)
            return self._solid_elsets

        name = f"{SET_PREFIX}_ALL_SOLID"
        self._set_lines.append(f"*ELSET, ELSET={name}")
        self._set_lines.extend(_format_id_block(sorted(solid_ids)))
        logger.info(
            "Mesh declared no solid element set; synthesised one",
            elset=name,
            elements=len(solid_ids),
        )
        self._solid_elsets = [name]
        return self._solid_elsets

    def _emit_constraints(self, load_case: LoadCase) -> None:
        """Write ``*BOUNDARY`` cards for every displacement constraint."""
        if not load_case.constraints:
            return

        self._emit("**", "** Displacement boundary conditions")
        cards: list[str] = []

        for index, constraint in enumerate(load_case.constraints, start=1):
            set_name, count = self._region_set(constraint.region, f"FIX{index}")
            dofs = constraint.resolved_dofs()
            for dof in dofs:
                cards.append(f"{set_name}, {dof}, {dof}, {_format_number(constraint.value)}")
            logger.debug(
                "Constrained region",
                case=load_case.name,
                region=constraint.region.describe(),
                nodes=count,
                dofs=dofs,
            )

        self._emit("*BOUNDARY", *cards)

    def _emit_point_loads(self, load_case: LoadCase) -> None:
        """Write ``*CLOAD`` cards for every concentrated force."""
        if not load_case.point_loads:
            return

        self._emit("**", "** Concentrated loads")
        cards: list[str] = []

        for index, load in enumerate(load_case.point_loads, start=1):
            components = load.components()
            if not components:
                logger.warning(
                    "Skipping point load with zero magnitude",
                    case=load_case.name,
                    region=load.region.describe(),
                )
                continue

            set_name, count = self._region_set(load.region, f"LOAD{index}")
            divisor = count if load.distribute else 1

            for dof, magnitude in components:
                cards.append(f"{set_name}, {dof}, {_format_number(magnitude / divisor)}")

            logger.debug(
                "Applied point load",
                case=load_case.name,
                region=load.region.describe(),
                nodes=count,
                distributed=load.distribute,
            )

        if cards:
            self._emit("*CLOAD", *cards)

    def _emit_distributed_loads(self, load_case: LoadCase) -> None:
        """Write ``*DLOAD`` cards for pressures and body acceleration."""
        cards: list[str] = []

        for pressure in load_case.pressures:
            try:
                self.mesh.resolve_element_set(pressure.element_set)
            except KeyError:
                available = ", ".join(sorted(self.mesh.element_sets)) or "none"
                raise DeckError(
                    f"Element set '{pressure.element_set}' not found in mesh. "
                    f"Available sets: {available}"
                ) from None
            cards.append(
                f"{pressure.element_set}, {pressure.face_label}, "
                f"{_format_number(pressure.magnitude_mpa)}"
            )

        if load_case.gravity_mm_s2:
            gravity = load_case.gravity_mm_s2
            if len(gravity) != 3:
                raise DeckError(f"gravity_mm_s2 must have exactly 3 components, got {len(gravity)}")
            magnitude = sum(g * g for g in gravity) ** 0.5
            if magnitude > 0.0:
                direction = [g / magnitude for g in gravity]
                for elset in self._solid_element_sets():
                    cards.append(
                        f"{elset}, GRAV, {_format_number(magnitude)}, "
                        + ", ".join(_format_number(d) for d in direction)
                    )

        if cards:
            self._emit("**", "** Distributed loads", "*DLOAD", *cards)

    def _emit_thermal_loads(self, load_case: LoadCase) -> None:
        """Write temperature boundaries, fluxes and film conditions."""
        if load_case.thermal_boundaries:
            self._emit("**", "** Prescribed temperatures")
            cards = []
            for index, boundary in enumerate(load_case.thermal_boundaries, start=1):
                set_name, _ = self._region_set(boundary.region, f"TEMP{index}")
                cards.append(
                    f"{set_name}, {TEMPERATURE_DOF}, {TEMPERATURE_DOF}, "
                    f"{_format_number(boundary.temperature_k)}"
                )
            self._emit("*BOUNDARY", *cards)

        if load_case.heat_fluxes:
            self._emit("**", "** Concentrated heat flux")
            cards = []
            for index, flux in enumerate(load_case.heat_fluxes, start=1):
                set_name, count = self._region_set(flux.region, f"FLUX{index}")
                divisor = count if flux.distribute else 1
                cards.append(
                    f"{set_name}, {TEMPERATURE_DOF}, {_format_number(flux.power_mw / divisor)}"
                )
            self._emit("*CFLUX", *cards)

        if load_case.convections:
            self._emit("**", "** Convective film conditions")
            cards = []
            for convection in load_case.convections:
                try:
                    self.mesh.resolve_element_set(convection.element_set)
                except KeyError:
                    available = ", ".join(sorted(self.mesh.element_sets)) or "none"
                    raise DeckError(
                        f"Element set '{convection.element_set}' not found in mesh. "
                        f"Available sets: {available}"
                    ) from None
                cards.append(
                    f"{convection.element_set}, {convection.face_label}, "
                    f"{_format_number(convection.sink_temperature_k)}, "
                    f"{_format_number(convection.film_coefficient)}"
                )
            self._emit("*FILM", *cards)

    def _emit_step(self, load_case: LoadCase, options: StepOptions) -> None:
        """Write the analysis step, its loads, and the output requests."""
        analysis = options.analysis

        step_line = "*STEP"
        if options.nlgeom and analysis == "static":
            step_line += ", NLGEOM"
        self._emit("**", f"** Analysis step: {analysis}", step_line)

        if analysis == "static":
            self._emit(
                "*STATIC",
                f"{_format_number(options.time_increment)}, {_format_number(options.time_period)}",
            )
        elif analysis == "modal":
            # Modal extraction is a property of the structure, so loads are not
            # applied -- only the constraints that define how it is held.
            self._emit("*FREQUENCY", f"{options.eigenmodes}")
        else:
            mode = ", STEADY STATE" if options.steady_state else ""
            self._emit(
                f"*HEAT TRANSFER{mode}",
                f"{_format_number(options.time_increment)}, {_format_number(options.time_period)}",
            )

        self._emit_constraints(load_case)

        if analysis == "static":
            self._emit_point_loads(load_case)
            self._emit_distributed_loads(load_case)
        elif analysis == "thermal":
            self._emit_thermal_loads(load_case)

        self._emit_output_requests(analysis)
        self._emit("*END STEP")

    def _emit_output_requests(self, analysis: str) -> None:
        """Request the fields the result parser reads.

        Without these cards CalculiX writes a ``.frd`` containing only the mesh.
        The solver still exits zero, so a missing output request looks exactly
        like a model with no stress in it.
        """
        self._emit("**", "** Output requests")

        if analysis == "thermal":
            self._emit("*NODE FILE", "NT")
            self._emit("*EL FILE", "HFL")
        elif analysis == "modal":
            self._emit("*NODE FILE", "U")
        else:
            self._emit("*NODE FILE", "U, RF")
            self._emit("*EL FILE", "S, E")

    # -- public API ----------------------------------------------------

    def build(self, load_case: LoadCase, options: StepOptions | None = None) -> str:
        """Render a complete deck for one load case.

        Args:
            load_case: The scenario to solve.
            options: Step settings; defaults to a linear static step.

        Returns:
            The deck text.

        Raises:
            DeckError: If the case cannot produce a meaningful solve -- no
                restraint, an empty region, an unknown set, or a mesh with no
                solid elements.
        """
        options = options or StepOptions()
        self._lines = []
        self._set_lines = []
        self._set_counter = 0
        self._generated_sets = {}
        self._solid_elsets = None

        _validate_case(load_case, options)

        # The step body is rendered first so that every region it resolves has
        # already appended its *NSET to the model-definition buffer; the buffers
        # are then concatenated in the order CalculiX requires.
        header: list[str] = []
        self._lines = header
        self._emit_heading(load_case, options)

        material_lines: list[str] = []
        self._lines = material_lines
        self._emit_material(thermal=options.analysis == "thermal")

        if options.analysis == "thermal":
            self._emit(
                "**",
                "** Initial conditions",
                "*INITIAL CONDITIONS, TYPE=TEMPERATURE",
                f"{self._all_nodes_set()}, {_format_number(options.initial_temperature_k)}",
            )

        step_lines: list[str] = []
        self._lines = step_lines
        self._emit_step(load_case, options)

        self._lines = [
            *header,
            *(["**", "** Generated node and element sets"] if self._set_lines else []),
            *self._set_lines,
            *material_lines,
            *step_lines,
        ]

        deck = "\n".join(self._lines) + "\n"

        logger.info(
            "Built CalculiX deck",
            case=load_case.name,
            analysis=options.analysis,
            material=self.material.key,
            generated_sets=len(self._generated_sets),
            lines=len(self._lines),
        )
        return deck

    def _all_nodes_set(self) -> str:
        """Return a node set covering the whole mesh, creating one if needed."""
        for name, ids in self.mesh.node_sets.items():
            if len(ids) == self.mesh.node_count:
                return name
        return self._emit_node_set(sorted(self.mesh.nodes), "ALLNODES")

    def write(
        self,
        load_case: LoadCase,
        output_path: str | Path,
        options: StepOptions | None = None,
    ) -> Path:
        """Build a deck and write it beside the mesh.

        Args:
            load_case: The scenario to solve.
            output_path: Destination ``.inp`` path.
            options: Step settings.

        Returns:
            The path written.
        """
        deck = self.build(load_case, options)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(deck, encoding="utf-8")

        logger.info("Wrote CalculiX deck", path=str(path), bytes=len(deck))
        return path


def _validate_case(load_case: LoadCase, options: StepOptions) -> None:
    """Reject load cases that cannot produce a meaningful solve.

    These checks run before any file is written so the error names the load case
    rather than surfacing later as a solver convergence failure.
    """
    if options.analysis in ("static", "modal") and not load_case.constraints:
        raise DeckError(
            f"Load case '{load_case.name}' has no displacement constraints. "
            "An unrestrained model has rigid body modes and produces a singular "
            "stiffness matrix."
        )

    if options.analysis == "static" and not load_case.has_mechanical_load():
        raise DeckError(
            f"Load case '{load_case.name}' applies no mechanical load. "
            "A static solve with no load yields zero stress everywhere."
        )

    if options.analysis == "thermal" and not load_case.has_thermal_load():
        raise DeckError(
            f"Load case '{load_case.name}' applies no thermal boundary conditions. "
            "A heat transfer solve needs at least one prescribed temperature, "
            "heat flux, or film condition."
        )


def parse_load_cases(raw: Any, default_name: str = "load_case") -> list[LoadCase]:
    """Normalise the load-case payload that arrives over MCP.

    Callers pass a list of dicts, a single dict, or a bare string naming a case.
    A bare string carries no physics, so it is rejected with an error that shows
    the expected shape rather than being silently solved as an empty case.

    Args:
        raw: The ``load_cases`` / ``load_case`` value from the tool arguments.
        default_name: Name to use for a case that does not supply one.

    Returns:
        Validated load cases.

    Raises:
        DeckError: If the payload cannot be read as one or more load cases.
    """
    if raw is None:
        return []

    if isinstance(raw, str):
        raise DeckError(
            f"Load case '{raw}' was given as a bare name, which carries no loads or "
            "constraints. Pass an object, e.g. "
            '{"name": "' + raw + '", "constraints": [{"region": {"face": "zmin"}}], '
            '"point_loads": [{"region": {"face": "zmax"}, "fz": -100.0}]}'
        )

    entries = raw if isinstance(raw, list) else [raw]
    cases: list[LoadCase] = []

    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, LoadCase):
            cases.append(entry)
            continue
        if not isinstance(entry, dict):
            raise DeckError(f"Load case {index} must be an object, got {type(entry).__name__}")

        payload = dict(entry)
        payload.setdefault("name", default_name if len(entries) == 1 else f"{default_name}_{index}")

        try:
            cases.append(LoadCase.model_validate(payload))
        except Exception as exc:
            raise DeckError(f"Invalid load case '{payload['name']}': {exc}") from exc

    return cases
