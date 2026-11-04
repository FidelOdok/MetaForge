"""Sketch interpretations and parametric sketches (the sketch compiler's records).

Three of the four sketch representations (FSG-007) have a model here; the
fourth, a generated raster image, is an ``image`` work product. Each has its
own authority:

``sketch_strokes``
    A person's raw strokes. A human sketch, not CAD (see the stroke recorder).
``SketchInterpretation`` (work product ``sketch_interpretation``)
    A **proposal**: primitives fitted to the strokes plus proposed, accepted
    and rejected constraints, all estimated. It is what the browser computed;
    the gateway validates its shape and re-checks its geometry, and nothing
    reads a measurement from it.
``ParametricSketch`` (work product ``parametric_sketch``)
    The **accepted** result a person promoted: the primitives with only the
    accepted constraints, re-checked by the gateway, and a feature plan whose
    every missing parameter (depth, angle, material) is ``required_input``.
    It never invents what the drawing does not say (SKP-005).

Everything is in the sketch's own 2D space. ``plane_id`` is carried through
untouched: assigning a physical plane is the sketch plane manager's job.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from twin_core.design_ir.models import (
    SKETCH_DIMENSION_TYPES,
    SketchArc,
    SketchCircle,
    SketchConstraint,
    SketchElement,
    SketchEllipse,
    SketchEntity,
    SketchLine,
    SketchSpline,
)
from twin_core.design_ir.models import SketchFrame as _IRSketchFrame  # noqa: E402

MAX_PRIMITIVES = 500
MAX_CONSTRAINTS = 3000
MAX_COORD = 1e7

_POINT_KEYS: dict[str, frozenset[str]] = {
    "line": frozenset({"start", "end"}),
    "arc": frozenset({"start", "end", "center"}),
    "circle": frozenset({"center"}),
    "ellipse": frozenset({"center"}),
    "rectangle": frozenset(),
    "spline": frozenset({"start", "end"}),
}


class SketchFrame(BaseModel):
    """How model coordinates map back to where they were drawn.

    ``model = origin + (drawn - origin) * scale``. The scale is 1 until a
    person confirms a first dimension, which scales the whole sketch.
    """

    scale: float = Field(default=1.0, gt=0, le=1e9)
    origin: tuple[float, float] = (0.0, 0.0)


class FitSummary(BaseModel):
    """What the browser's fit reported (SKP-001: the screen-to-sketch mapping it used)."""

    extent: float = Field(ge=0)
    tolerance: float = Field(gt=0)
    unit_px: float = Field(gt=0)
    stroke_count: int = Field(ge=0)
    skipped_stroke_ids: list[str] = Field(default_factory=list, max_length=2000)
    duration_ms: float = Field(default=0.0, ge=0)


class SolveSummary(BaseModel):
    """What the browser's solve reported. Informational: the gateway re-checks."""

    status: Literal["fully", "under", "over", "not_solved"]
    dof: int = Field(ge=0)
    shape_dof: int = Field(ge=0)
    converged: bool
    iterations: int = Field(default=0, ge=0)
    max_residual: float = Field(default=0.0, ge=0)
    redundant: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    message: str = Field(default="", max_length=1000)
    duration_ms: float = Field(default=0.0, ge=0)


class ClosedProfile(BaseModel):
    primitive_ids: list[str] = Field(min_length=1, max_length=MAX_PRIMITIVES)


def _split(ref: str) -> tuple[str, str | None]:
    head, dot, key = ref.rpartition(".")
    if dot and key in ("start", "end", "center"):
        return head, key
    return ref, None


def _finite_coords(el: SketchElement) -> bool:
    vals: list[float] = []
    for name in ("start", "end", "center", "origin"):
        v = getattr(el, name, None)
        if v is not None:
            vals.extend(v)
    if isinstance(el, SketchSpline):
        for p in el.points:
            vals.extend(p)
    for name in ("radius", "major_radius", "minor_radius", "width", "height"):
        v = getattr(el, name, None)
        if v is not None:
            vals.append(v)
    return all(v == v and abs(v) <= MAX_COORD for v in vals)


def validate_sketch_refs(
    elements: list[SketchElement], constraints: list[SketchConstraint]
) -> list[str]:
    """Problems with ids and refs: missing or repeated ids, refs to nothing, wrong point names."""
    errors: list[str] = []
    types: dict[str, str] = {}
    for i, el in enumerate(elements):
        if not el.id:
            errors.append(f"primitive {i} has no id")
            continue
        if el.id in types:
            errors.append(f"primitive id {el.id} is used twice")
        types[el.id] = el.type
        if not _finite_coords(el):
            errors.append(f"primitive {el.id} has a coordinate that is not a finite number")
    seen: set[str] = set()
    for c in constraints:
        if c.id in seen:
            errors.append(f"constraint id {c.id} is used twice")
        seen.add(c.id)
        for ref in c.refs:
            eid, key = _split(ref)
            kind = types.get(eid)
            if kind is None:
                errors.append(f"constraint {c.id} refers to {eid}, which is not in the sketch")
            elif key is not None and key not in _POINT_KEYS[kind]:
                errors.append(f"constraint {c.id}: {ref} is not a point of a {kind}")
    return errors


class SketchInterpretation(BaseModel):
    """A proposal: fitted primitives and constraints from one sketch revision."""

    #: The client's id for this proposal. Storing it again is idempotent.
    interpretation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    #: The ``sketch_strokes`` revision it was made from (``SKT-...@n``).
    sketch_ref: str | None = Field(default=None, max_length=200)
    plane_id: str | None = Field(default=None, max_length=200)
    space: Literal["sketch_unit"] = "sketch_unit"
    unit: Literal["unknown", "mm", "in"] = "unknown"
    frame: SketchFrame = Field(default_factory=SketchFrame)
    primitives: list[SketchElement] = Field(default_factory=list, max_length=MAX_PRIMITIVES)
    constraints: list[SketchConstraint] = Field(default_factory=list, max_length=MAX_CONSTRAINTS)
    profiles: list[ClosedProfile] = Field(default_factory=list, max_length=MAX_PRIMITIVES)
    fit: FitSummary | None = None
    solve: SolveSummary | None = None

    @field_validator("primitives")
    @classmethod
    def _fitted_are_estimated(cls, v: list[SketchElement]) -> list[SketchElement]:
        for el in v:
            if el.source_stroke_ids and not el.estimated:
                raise ValueError(
                    f"primitive {el.id} was fitted to strokes, so it must be marked estimated"
                )
        return v

    @model_validator(mode="after")
    def _refs_resolve(self) -> SketchInterpretation:
        errors = validate_sketch_refs(self.primitives, self.constraints)
        ids = {el.id for el in self.primitives}
        for prof in self.profiles:
            for pid in prof.primitive_ids:
                if pid not in ids:
                    errors.append(f"a profile refers to {pid}, which is not in the sketch")
        for c in self.constraints:
            if c.type in SKETCH_DIMENSION_TYPES and c.source == "human_confirmed":
                continue
            if c.type in SKETCH_DIMENSION_TYPES and c.unit not in (None, "unknown", self.unit):
                errors.append(f"dimension {c.id} is in {c.unit}, but the sketch is in {self.unit}")
        if errors:
            raise ValueError("; ".join(errors[:10]))
        return self

    def accepted(self) -> list[SketchConstraint]:
        return [c for c in self.constraints if c.status == "accepted"]


FeatureOp = Literal["extrude", "revolve"]


class FeatureParameter(BaseModel):
    """A feature parameter. Missing ones are ``required_input``, never guessed."""

    status: Literal["required_input", "provided"] = "required_input"
    value: float | str | None = None
    unit: str | None = None
    question: str = ""


class FeaturePlanItem(BaseModel):
    """A candidate CAD feature from a closed profile (SKP-005)."""

    id: str
    op: FeatureOp
    profile_primitive_ids: list[str]
    axis_id: str | None = None
    parameters: dict[str, FeatureParameter]
    note: str = ""


def feature_plan(interp: SketchInterpretation) -> list[FeaturePlanItem]:
    """Candidate extrude and revolve features, every missing parameter marked required.

    One extrude per closed profile, and a revolve when the sketch has an
    accepted axis line. Depth, angle and material are never read off a
    drawing: they are always ``required_input`` here.
    """
    plan: list[FeaturePlanItem] = []
    material = FeatureParameter(question="What material is it made of?")
    for i, prof in enumerate(interp.profiles):
        plan.append(
            FeaturePlanItem(
                id=f"feat-extrude-{i + 1}",
                op="extrude",
                profile_primitive_ids=prof.primitive_ids,
                parameters={
                    "depth": FeatureParameter(
                        unit=interp.unit, question="How thick should this be?"
                    ),
                    "material": material,
                },
                note="A candidate. The sketch does not say how deep it goes.",
            )
        )
    axes = [el for el in interp.primitives if isinstance(el, SketchLine) and el.construction]
    if axes and interp.profiles:
        plan.append(
            FeaturePlanItem(
                id="feat-revolve-1",
                op="revolve",
                profile_primitive_ids=interp.profiles[0].primitive_ids,
                axis_id=axes[0].id,
                parameters={
                    "angle": FeatureParameter(unit="deg", question="How far round should it turn?"),
                    "material": material,
                },
                note="A candidate, about the sketch's axis.",
            )
        )
    return plan


class ParametricSketch(BaseModel):
    """The promoted, accepted sketch: what a person stands behind."""

    interpretation_ref: str | None = None
    sketch_ref: str | None = None
    plane_id: str | None = None
    space: Literal["sketch_unit"] = "sketch_unit"
    unit: Literal["unknown", "mm", "in"] = "unknown"
    frame: SketchFrame = Field(default_factory=SketchFrame)
    elements: list[SketchElement]
    #: Accepted constraints only.
    constraints: list[SketchConstraint]
    profiles: list[ClosedProfile] = Field(default_factory=list)
    feature_plan: list[FeaturePlanItem] = Field(default_factory=list)

    def to_sketch_entity(
        self,
        entity_id: str,
        body_ref: str,
        *,
        plane: Literal["XY", "XZ", "YZ"] = "XY",
        offset: float = 0.0,
        frame: dict[str, Any] | None = None,
    ) -> SketchEntity:
        """This sketch as a Design IR ``sketch`` entity on ``body_ref``.

        Only for a sketch in millimetres (the IR's unit): a sketch of unknown
        scale is not CAD, so it does not become IR. Construction geometry is
        kept (the Lowering Passes skip it). Sketch space has y down the
        screen; on a plane ``u = x`` and ``v = -y`` (the plane manager's
        convention), so points are mirrored in v here and arcs and ellipses
        follow. ``plane`` and ``offset`` come from the sketch's confirmed
        plane (``SketchPlane.design_ir_fields``).
        """
        if self.unit != "mm":
            raise ValueError(
                f"the sketch's unit is {self.unit}; it must be in millimetres to be used as CAD"
            )
        return SketchEntity(
            id=entity_id,
            body_ref=body_ref,
            plane=plane,
            offset=offset,
            elements=[_to_plane(el) for el in self.elements],
            constraints=list(self.constraints),
            plane_id=self.plane_id,
            frame=_IRSketchFrame.model_validate(frame) if frame is not None else None,
        )


def _flip(p: tuple[float, float]) -> tuple[float, float]:
    return (p[0], -p[1])


def _to_plane(el: SketchElement) -> SketchElement:
    """One element from sketch space (y down) to plane space (v up)."""
    if isinstance(el, SketchLine):
        return el.model_copy(update={"start": _flip(el.start), "end": _flip(el.end)})
    if isinstance(el, SketchCircle):
        return el.model_copy(update={"center": _flip(el.center)})
    if isinstance(el, SketchArc):
        # Mirroring reverses the direction: the old end is the new start.
        return el.model_copy(
            update={
                "center": _flip(el.center),
                "start_angle": (-el.end_angle) % 360,
                "end_angle": (-el.start_angle) % 360,
            }
        )
    if isinstance(el, SketchEllipse):
        return el.model_copy(update={"center": _flip(el.center), "rotation": -el.rotation})
    if isinstance(el, SketchSpline):
        return el.model_copy(update={"points": [_flip(p) for p in el.points]})
    # SketchRectangle: the origin is the corner with the smallest v.
    return el.model_copy(update={"origin": (el.origin[0], -(el.origin[1] + el.height))})


def promote(interp: SketchInterpretation, *, interpretation_ref: str | None) -> ParametricSketch:
    """The parametric sketch for an interpretation: its accepted constraints and a feature plan."""
    return ParametricSketch(
        interpretation_ref=interpretation_ref,
        sketch_ref=interp.sketch_ref,
        plane_id=interp.plane_id,
        unit=interp.unit,
        frame=interp.frame,
        elements=list(interp.primitives),
        constraints=interp.accepted(),
        profiles=list(interp.profiles),
        feature_plan=feature_plan(interp),
    )


__all__ = [
    "ClosedProfile",
    "FeatureParameter",
    "FeaturePlanItem",
    "FitSummary",
    "ParametricSketch",
    "SketchFrame",
    "SketchInterpretation",
    "SolveSummary",
    "feature_plan",
    "promote",
    "validate_sketch_refs",
]
