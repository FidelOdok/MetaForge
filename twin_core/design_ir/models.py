"""Design IR entity types and the document container.

Every operation from the requirements doc's tiers 6.2.1-6.2.6 (primitive
solids, parametric templates, feature-tree, boolean, transform, assembly)
is one entity type here. Tiers 6.2.7/6.2.8 (measurement/export) and 6.2.9
(the execute_script/execute_code escape hatch) are deliberately not
represented -- they don't append to a feature history in either real
adapter, and the escape hatch is already a separate, already-shipping
mechanism (``generate_cad_script``) that produces geometry outside this
system entirely.

Field lists here are reconciled against the real adapter signatures in
``tool_registry/tools/freecad/adapter.py`` (session_id/body_id-taking
calls), not the requirements doc's own "(proposed)" placeholders, which
omitted ``body_ref``/``assembly_ref`` on every op that actually needs one.

Dependencies between entities are reference fields (``sketch_ref``,
``body_ref``, ``tool_refs``, ...), not a separate edge collection -- see
``twin_core/design_ir/validation.py`` for how these are resolved. Every
such field name ends in ``_ref`` (single) or ``_refs`` (plural); that
suffix convention is itself the mechanism referential-integrity checking
uses to find them, so a new entity type participates automatically
without a hand-maintained field registry. A ``_ref``/``_refs`` value is
always a plain entity id (``"sol1"``) -- the id of another entity earlier
in the same document, checked by ``validation.py``.

Edge/face selectors (``edge_selectors`` on ``FilletEdgesEntity``/
``ChamferEdgesEntity``, ``face_selectors`` on ``ShellEntity``) are a
different thing entirely, deliberately *not* named ``*_ref``/``*_refs``:
they're real FreeCAD topology names (``"Edge3"``, ``"Face2"``) scoped to
the single body a `body_ref` on the same entity already identifies, not
references to another entity in this document. An earlier revision of
this schema modeled them as dotted entity references (the requirements
doc's illustrative ``"sol1.top_front_edge"``) and included them in the
referential-integrity scan; checked against the real implementation
(``tool_registry/tools/freecad/operations.py:838-862``), that was wrong,
there is no entity-id prefix to extract, "Edge3" doesn't reference
anything else in the graph. Resolving what "Edge3" actually corresponds
to across a recompute is the topological-naming problem (requirements
doc §6.2.3/§6.6.4), a Lowering Pass/OCCT-`TNaming` concern, not something
this schema tracks or validates.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Vec3 = tuple[float, float, float]


class IREntityBase(BaseModel):
    """Common fields for every IR entity.

    ``id`` is a caller-assigned string, not a UUID -- deliberately
    different from ``twin_core.models.base.NodeBase.id``. A NodeBase id
    identifies a node in the live, multi-user Digital Twin graph; an IR
    entity's id is local to one document, assigned by whichever agent
    authored it, matching every worked example in the requirements doc
    (short mnemonic strings an LLM can reference consistently within one
    generation). If an entity is later promoted into a real Twin node,
    that gets its own separate UUID at that point.
    """

    id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# 6.2.1 Primitive solids (FreeCAD only)
# ---------------------------------------------------------------------------


class CreatePrimitiveEntity(IREntityBase):
    """A plain Part primitive: box, cylinder, sphere, cone, or torus."""

    op: Literal["create_primitive"] = "create_primitive"
    kind: Literal["box", "cylinder", "sphere", "cone", "torus"]
    parameters: dict[str, float] = Field(default_factory=dict)
    name: str = ""


# ---------------------------------------------------------------------------
# 6.2.2 Parametric template shapes (both adapters)
# ---------------------------------------------------------------------------


class CreateParametricEntity(IREntityBase):
    """A named template shape (bracket/plate/enclosure/cylinder).

    Same ``shape_type`` enum on both adapters (§6.2.2, confirmed identical).
    """

    op: Literal["create_parametric"] = "create_parametric"
    shape_type: Literal["bracket", "plate", "enclosure", "cylinder"]
    dimensions: dict[str, float] = Field(default_factory=dict)
    material: str = ""


# ---------------------------------------------------------------------------
# 6.2.3 Sketch / feature-tree (FreeCAD only, PartDesign::Body + Sketcher)
# ---------------------------------------------------------------------------


class SketchLine(BaseModel):
    type: Literal["line"] = "line"
    start: tuple[float, float]
    end: tuple[float, float]


class SketchCircle(BaseModel):
    type: Literal["circle"] = "circle"
    center: tuple[float, float]
    radius: float = Field(gt=0)


class SketchRectangle(BaseModel):
    type: Literal["rectangle"] = "rectangle"
    origin: tuple[float, float]
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class SketchArc(BaseModel):
    type: Literal["arc"] = "arc"
    center: tuple[float, float]
    radius: float = Field(gt=0)
    start_angle: float
    end_angle: float


SketchElement = Annotated[
    SketchLine | SketchCircle | SketchRectangle | SketchArc,
    Field(discriminator="type"),
]


class CreateBodyEntity(IREntityBase):
    """A PartDesign::Body container -- other feature-tree ops attach to it via body_ref."""

    op: Literal["create_body"] = "create_body"
    name: str = ""


class SketchEntity(IREntityBase):
    op: Literal["sketch"] = "sketch"
    body_ref: str
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    offset: float = 0.0
    elements: list[SketchElement] = Field(default_factory=list)


class PadEntity(IREntityBase):
    op: Literal["pad"] = "pad"
    body_ref: str
    sketch_ref: str
    depth: float = Field(gt=0)
    reversed: bool = False
    midplane: bool = False


class PocketEntity(IREntityBase):
    op: Literal["pocket"] = "pocket"
    body_ref: str
    sketch_ref: str
    depth: float = Field(gt=0)
    reversed: bool = False


class RevolveEntity(IREntityBase):
    op: Literal["revolve"] = "revolve"
    body_ref: str
    sketch_ref: str
    axis: Literal["V", "H"] = "V"
    angle: float = Field(default=360.0, gt=0, le=360.0)
    reversed: bool = False


class LoftEntity(IREntityBase):
    op: Literal["loft"] = "loft"
    body_ref: str
    profile_ref: str
    section_refs: list[str] = Field(default_factory=list, min_length=1)


class SweepEntity(IREntityBase):
    op: Literal["sweep"] = "sweep"
    body_ref: str
    profile_ref: str
    path_ref: str


class FilletEdgesEntity(IREntityBase):
    """PartDesign fillet on a body's tip.

    ``edge_selectors`` are real FreeCAD edge names (``"Edge3"``, ...), scoped
    to ``body_ref``'s tip shape -- not entity references (confirmed against
    ``tool_registry/tools/freecad/operations.py:838-849``), so this field is
    deliberately not named ``edge_refs``: it must NOT be picked up by
    ``validation.py``'s ``_ref``/``_refs`` referential-integrity scan, there
    is no prior entity for "Edge3" to resolve against. Empty = every edge of
    the tip.
    """

    op: Literal["fillet_edges"] = "fillet_edges"
    body_ref: str
    radius: float = Field(gt=0)
    edge_selectors: list[str] = Field(default_factory=list)


class ChamferEdgesEntity(IREntityBase):
    """PartDesign chamfer on a body's tip.

    See ``FilletEdgesEntity.edge_selectors`` -- same "real FreeCAD selector,
    not an entity reference" reasoning applies here.
    """

    op: Literal["chamfer_edges"] = "chamfer_edges"
    body_ref: str
    distance: float = Field(gt=0)
    edge_selectors: list[str] = Field(default_factory=list)


class LinearPatternEntity(IREntityBase):
    op: Literal["linear_pattern"] = "linear_pattern"
    body_ref: str
    source_ref: str
    axis: Literal["X", "Y", "Z"] = "X"
    count: int = Field(gt=1)
    spacing: float = Field(gt=0)


class PolarPatternEntity(IREntityBase):
    op: Literal["polar_pattern"] = "polar_pattern"
    body_ref: str
    source_ref: str
    axis: Literal["X", "Y", "Z"] = "Z"
    count: int = Field(gt=1)
    angle: float = Field(default=360.0, gt=0, le=360.0)


class MirrorEntity(IREntityBase):
    op: Literal["mirror"] = "mirror"
    body_ref: str
    source_ref: str
    plane: Literal["XY", "XZ", "YZ"] = "XY"


class ShellEntity(IREntityBase):
    """Hollows a body's tip.

    ``face_selectors`` are real FreeCAD face names (``"Face2"``, ...), scoped
    to ``body_ref``'s tip shape -- not entity references, same reasoning as
    ``FilletEdgesEntity.edge_selectors``. Empty = FreeCAD's own default face
    selection for ``makeThickness``.
    """

    op: Literal["shell"] = "shell"
    body_ref: str
    thickness: float = Field(gt=0)
    face_selectors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 6.2.4 Boolean (both adapters) -- Plain Part tier, no body
# ---------------------------------------------------------------------------


class BooleanEntity(IREntityBase):
    op: Literal["boolean"] = "boolean"
    operation: Literal["union", "subtract", "intersect"]
    base_ref: str
    tool_refs: list[str] = Field(min_length=1)
    keep_originals: bool = False


class FilletEntity(IREntityBase):
    """Plain Part fillet -- rounds every edge of target_ref unconditionally, no body."""

    op: Literal["fillet"] = "fillet"
    target_ref: str
    radius: float = Field(gt=0)


class ChamferEntity(IREntityBase):
    """Plain Part chamfer -- bevels every edge of target_ref unconditionally, no body."""

    op: Literal["chamfer"] = "chamfer"
    target_ref: str
    distance: float = Field(gt=0)


# ---------------------------------------------------------------------------
# 6.2.5 Transform (FreeCAD only, Plain Part tier)
# ---------------------------------------------------------------------------


class TransformEntity(IREntityBase):
    op: Literal["transform"] = "transform"
    target_ref: str
    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec3 | None = None  # euler angles in degrees; None = no rotation


# ---------------------------------------------------------------------------
# 6.2.6 Assembly (both adapters for placement; FreeCAD only for typed joints)
# ---------------------------------------------------------------------------


class CreateAssemblyEntity(IREntityBase):
    op: Literal["create_assembly"] = "create_assembly"
    name: str = ""


class PlaceEntity(IREntityBase):
    op: Literal["place"] = "place"
    assembly_ref: str
    part_ref: str
    position: Vec3 = (0.0, 0.0, 0.0)
    orientation: Vec3 | None = None


class JointEntity(IREntityBase):
    """A typed kinematic joint between two parts.

    Deliberately just another entity in the same flat ``entities`` list,
    with its own id -- resolving the gap the Fusion 360 Gallery dataset
    has (joints embedded in a body-pair record with no independent
    identity). This gives joints real identity, diffability, and
    in-place editability for free, no separate joints collection needed.
    """

    op: Literal["joint"] = "joint"
    assembly_ref: str
    part_a_ref: str
    part_b_ref: str
    joint_type: Literal["fixed", "revolute", "slider", "cylindrical", "ball"]
    axis: Vec3 | None = None
    anchor: Vec3 | None = None


IREntity = Annotated[
    CreatePrimitiveEntity
    | CreateParametricEntity
    | CreateBodyEntity
    | SketchEntity
    | PadEntity
    | PocketEntity
    | RevolveEntity
    | LoftEntity
    | SweepEntity
    | FilletEdgesEntity
    | ChamferEdgesEntity
    | LinearPatternEntity
    | PolarPatternEntity
    | MirrorEntity
    | ShellEntity
    | BooleanEntity
    | FilletEntity
    | ChamferEntity
    | TransformEntity
    | CreateAssemblyEntity
    | PlaceEntity
    | JointEntity,
    Field(discriminator="op"),
]


class DesignIR(BaseModel):
    """A versioned, schema-validated parametric design document.

    ``entities`` is the one feature tree -- ordered, and that order is a
    valid topological order by construction: a ``*_ref``/``*_refs`` field
    may only point to an entity appearing earlier in this list (enforced
    in ``validation.py``, not here, since it needs the whole document).
    """

    schema_version: str = "0.1.0"
    units: Literal["mm"] = "mm"
    entities: list[IREntity] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
