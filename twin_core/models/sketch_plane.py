"""The plane a sketch is drawn on, as a right-handed frame (FORGE-790).

A sketch is drawn before anyone says where it sits in space (FRS PLN-001).
When it needs a physical meaning (it is made editable, or used in 3D), a
person assigns it a plane. This module is that plane: a stable id, the
component that owns it, an orthonormal right-handed frame (origin, U, V and
the normal ``U x V``), the 4x4 transform that frame implies, the unit of the
frame, the sketch revision it was set against, a face attachment when it sits
on a model face, a lifecycle state, and who proposed it with what confidence
(PLN-004, PLN-012).

Frame conventions (the same names the design IR's ``SketchEntity.plane``
uses, with CadQuery's named-plane axes):

=========  =========  ======  ======  ======
Principal  View name  U       V       Normal
=========  =========  ======  ======  ======
``XY``     Top        +X      +Y      +Z
``XZ``     Front      +X      +Z      -Y
``YZ``     Side       +Y      +Z      +X
=========  =========  ======  ======  ======

Every frame is right-handed: ``normal == u x v``. Front looks at the product
from -Y toward +Y, so its normal points back at the viewer (-Y); Side looks
from +X; Top looks down from +Z.

A sketch's own points are in *sketch space* (``x`` right, ``y`` down, as on
the screen it was drawn on). Local plane coordinates are ``u = x`` and
``v = -y``, so "up" on the screen is +V on the plane. World is
``origin + u * U + v * V``.

The unit is the unit of the frame (the origin and any offset). ``unknown`` is
allowed and is never converted: :meth:`SketchPlane.convert_length` refuses it.
The sketch's own scale (model units per sketch unit) is ``scale``; ``None``
means unknown, which is the case for every freehand sketch until a person
calibrates it (PLN-013).

Offset and custom planes are named in :class:`PlaneKind` so stored data can
carry them later, but a plane of either kind is refused today. A face plane
carries a :class:`FaceAttachment` with the face's geometry signature, which
:mod:`twin_core.geometry.face_signature` uses to find the face again when
the CAD item moves on (CDE slice 9).

This module is pure (Pydantic and the standard library), so the gateway, the
twin and tests share one definition of a valid frame.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Vec3 = tuple[float, float, float]
Matrix4 = list[list[float]]

#: Length and angle tolerances. Generous enough for JSON round trips, far
#: below anything a person could see.
AXIS_TOLERANCE = 1e-6
TRANSFORM_TOLERANCE = 1e-6


class PlaneKind(StrEnum):
    UNASSIGNED = "unassigned"
    PRINCIPAL = "principal"
    FACE = "face"
    #: Reserved: a plane derived from a parent frame at a distance.
    OFFSET = "offset"
    #: Reserved: an explicit origin and orientation.
    CUSTOM = "custom"


#: Kinds that can be stored today.
SUPPORTED_KINDS = frozenset({PlaneKind.UNASSIGNED, PlaneKind.PRINCIPAL, PlaneKind.FACE})


class PrincipalPlane(StrEnum):
    XY = "XY"
    XZ = "XZ"
    YZ = "YZ"


class PlaneUnit(StrEnum):
    MM = "mm"
    IN = "in"
    UNKNOWN = "unknown"


class PlaneState(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    ATTACHED = "ATTACHED"
    INVALID = "INVALID"
    REATTACHMENT_REQUIRED = "REATTACHMENT_REQUIRED"
    ARCHIVED = "ARCHIVED"


#: States in which the plane places the sketch in 3D.
PLACED_STATES = frozenset({PlaneState.CONFIRMED, PlaneState.ATTACHED})


class ProposedBy(StrEnum):
    """Where a plane came from, in the order inference consults them (PLN-002)."""

    USER = "user"
    FACE = "face"
    EXISTING_SKETCH = "existing_sketch"
    VIEW = "view"
    COMPONENT_FRAME = "component_frame"
    MODEL = "model"


#: The inference order: an earlier source always outranks a later one.
PROPOSAL_ORDER: tuple[ProposedBy, ...] = tuple(ProposedBy)

#: The person-facing name of each principal plane.
VIEW_NAMES: dict[PrincipalPlane, str] = {
    PrincipalPlane.XZ: "Front",
    PrincipalPlane.YZ: "Side",
    PrincipalPlane.XY: "Top",
}

#: ``(u, v, normal)`` per principal plane. See the module docstring.
PRINCIPAL_FRAMES: dict[PrincipalPlane, tuple[Vec3, Vec3, Vec3]] = {
    PrincipalPlane.XY: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    PrincipalPlane.XZ: ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    PrincipalPlane.YZ: ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}

_MM_PER: dict[PlaneUnit, float] = {PlaneUnit.MM: 1.0, PlaneUnit.IN: 25.4}


class UnknownUnitError(ValueError):
    """A length in an unknown unit was asked to become a known one."""


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def frame_matrix(origin: Vec3, u: Vec3, v: Vec3, n: Vec3) -> Matrix4:
    """The 4x4 local-to-world transform, row-major: columns are U, V, N, origin."""
    return [
        [u[0], v[0], n[0], origin[0]],
        [u[1], v[1], n[1], origin[1]],
        [u[2], v[2], n[2], origin[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _vec(value: Any, name: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must be three numbers")
    out = tuple(float(c) for c in value)
    if not all(math.isfinite(c) for c in out):
        raise ValueError(f"{name} must be finite numbers")
    return (out[0], out[1], out[2])


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class FaceAttachment(BaseModel):
    """Where a face-attached plane sits on a model (CDE slice 9).

    ``item_key`` and ``revision`` name the CAD revision; ``face_ref`` is the
    face id ``cadquery.describe_faces`` gave on that revision (meaningful on
    that revision only). ``signature`` is the face's geometry signature
    (:class:`twin_core.geometry.face_signature.FaceSignature`): the semantic
    tag, normal, offset, area, centroid, loop edge count and adjacent normals
    that find the face again when the CAD item moves on (PLN-009).
    ``remapped_from`` is the attachment this one was found from by a remap,
    as ``KEY@n#face``. While the plane is ``REATTACHMENT_REQUIRED`` the
    attachment and frame are the last valid ones, and ``pending`` says what
    the remap saw (outcome, candidate faces, the revision it looked at).
    """

    model_config = ConfigDict(extra="forbid")

    item_key: str = Field(min_length=1, max_length=120)
    revision: int | None = Field(default=None, ge=1)
    face_ref: str = Field(min_length=1, max_length=200)
    signature: dict[str, Any] | None = None
    remapped_from: str | None = Field(default=None, max_length=300)
    pending: dict[str, Any] | None = None


class SketchPlane(BaseModel):
    """One sketch plane. See the module docstring for the conventions."""

    model_config = ConfigDict(extra="forbid")

    plane_id: str = Field(min_length=1, max_length=120)
    owner_component: str | None = Field(default=None, max_length=120)
    kind: PlaneKind = PlaneKind.UNASSIGNED
    principal: PrincipalPlane | None = None
    origin: Vec3 = (0.0, 0.0, 0.0)
    u_axis: Vec3 = (1.0, 0.0, 0.0)
    v_axis: Vec3 = (0.0, 1.0, 0.0)
    normal: Vec3 = (0.0, 0.0, 1.0)
    unit: PlaneUnit = PlaneUnit.UNKNOWN
    #: Model units per sketch unit; ``None`` is an unknown scale (PLN-013).
    scale: float | None = Field(default=None, gt=0)
    #: Row-major 4x4 local-to-world transform. Derived when left out.
    transform: Matrix4 | None = None
    #: The sketch revision (``SKT-...@n``) the plane was set against.
    source_revision: str | None = Field(default=None, max_length=200)
    attachment: FaceAttachment | None = None
    state: PlaneState = PlaneState.UNASSIGNED
    proposed_by: ProposedBy | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("origin", "u_axis", "v_axis", "normal", mode="before")
    @classmethod
    def _three_finite(cls, v: Any, info: Any) -> Vec3:
        return _vec(v, info.field_name)

    @model_validator(mode="after")
    def _valid_frame(self) -> Self:
        if self.kind not in SUPPORTED_KINDS:
            raise ValueError(f"{self.kind.value} planes are reserved for a later release")
        u, v, n = self.u_axis, self.v_axis, self.normal
        for name, axis in (("u_axis", u), ("v_axis", v), ("normal", n)):
            if abs(norm(axis) - 1.0) > AXIS_TOLERANCE:
                raise ValueError(f"{name} must be a unit vector (length {norm(axis):.6g})")
        if abs(dot(u, v)) > AXIS_TOLERANCE:
            raise ValueError("u_axis and v_axis must be orthogonal")
        uxv = cross(u, v)
        if max(abs(uxv[i] - n[i]) for i in range(3)) > AXIS_TOLERANCE:
            raise ValueError(
                "normal must equal u_axis x v_axis (a right-handed frame); "
                f"u x v is {tuple(round(c, 6) for c in uxv)}"
            )

        expected = frame_matrix(self.origin, u, v, n)
        if self.transform is None:
            self.transform = expected
        else:
            t = self.transform
            if len(t) != 4 or any(len(row) != 4 for row in t):
                raise ValueError("transform must be a 4x4 matrix (row-major)")
            for i in range(4):
                for j in range(4):
                    if not math.isfinite(t[i][j]):
                        raise ValueError("transform must be finite numbers")
                    if abs(t[i][j] - expected[i][j]) > TRANSFORM_TOLERANCE:
                        raise ValueError(
                            "transform does not match origin and axes: its columns must be "
                            "u_axis, v_axis, normal and origin"
                        )

        if self.kind is PlaneKind.PRINCIPAL:
            if self.principal is None:
                raise ValueError("a principal plane names its principal: XY, XZ or YZ")
            pu, pv, pn = PRINCIPAL_FRAMES[self.principal]
            if any(
                max(abs(a[i] - b[i]) for i in range(3)) > AXIS_TOLERANCE
                for a, b in ((u, pu), (v, pv), (n, pn))
            ):
                raise ValueError(
                    f"a {self.principal.value} principal plane has the axes "
                    f"u={pu}, v={pv}, normal={pn}"
                )
        elif self.principal is not None:
            raise ValueError("only a principal plane names a principal")

        if self.kind is PlaneKind.FACE and self.attachment is None:
            raise ValueError("a face plane needs its attachment (item, revision, face)")
        if self.kind is not PlaneKind.FACE and self.attachment is not None:
            raise ValueError("only a face plane carries an attachment")

        unassigned = self.kind is PlaneKind.UNASSIGNED
        if unassigned and self.state not in (PlaneState.UNASSIGNED, PlaneState.ARCHIVED):
            raise ValueError(f"an unassigned plane cannot be {self.state.value}")
        if not unassigned and self.state is PlaneState.UNASSIGNED:
            raise ValueError("a plane with a frame is not UNASSIGNED")
        if self.state is PlaneState.ATTACHED and self.kind is not PlaneKind.FACE:
            raise ValueError("only a face plane is ATTACHED; a principal plane is CONFIRMED")
        if self.scale is not None and self.unit is PlaneUnit.UNKNOWN:
            raise ValueError("a known scale needs a known unit (mm or in)")
        return self

    # -- reading -------------------------------------------------------------

    @property
    def placed(self) -> bool:
        """True when the plane puts the sketch somewhere in 3D."""
        return self.state in PLACED_STATES

    @property
    def view_name(self) -> str | None:
        return VIEW_NAMES.get(self.principal) if self.principal else None

    def to_world(self, u: float, v: float, w: float = 0.0) -> Vec3:
        """Local ``(u, v, w)`` in frame units to world."""
        o, a, b, n = self.origin, self.u_axis, self.v_axis, self.normal
        return (
            o[0] + u * a[0] + v * b[0] + w * n[0],
            o[1] + u * a[1] + v * b[1] + w * n[1],
            o[2] + u * a[2] + v * b[2] + w * n[2],
        )

    def to_local(self, p: Vec3) -> Vec3:
        """World ``p`` to local ``(u, v, w)``; ``w`` is the distance off the plane."""
        d = (p[0] - self.origin[0], p[1] - self.origin[1], p[2] - self.origin[2])
        return (dot(d, self.u_axis), dot(d, self.v_axis), dot(d, self.normal))

    def convert_length(self, value: float, to: PlaneUnit) -> float:
        """``value`` in this plane's unit, in ``to``. An unknown unit is never converted."""
        if self.unit is PlaneUnit.UNKNOWN or to is PlaneUnit.UNKNOWN:
            if self.unit is to:
                return value
            raise UnknownUnitError(
                "this plane's unit is unknown; a length cannot be converted until a person "
                "sets the unit"
            )
        return value * _MM_PER[self.unit] / _MM_PER[to]

    def design_ir_fields(self) -> dict[str, Any]:
        """``{"plane", "offset"}`` (and ``frame``) for a design-IR ``SketchEntity``.

        A confirmed principal plane maps to its principal and the offset along
        its normal. An attached face plane maps to an explicit ``frame``
        (origin, U as the in-plane X, and the normal). Nothing else maps.
        """
        if self.kind is PlaneKind.FACE and self.state is PlaneState.ATTACHED:
            return {
                "plane": "XY",
                "offset": 0.0,
                "frame": {"origin": self.origin, "x_dir": self.u_axis, "normal": self.normal},
            }
        if self.kind is not PlaneKind.PRINCIPAL or self.principal is None or not self.placed:
            raise ValueError(
                "only a confirmed principal plane or an attached face plane maps to a "
                f"design-IR sketch (this one is {self.kind.value}, {self.state.value})"
            )
        return {"plane": self.principal.value, "offset": dot(self.origin, self.normal)}


def principal_plane(
    principal: PrincipalPlane | str,
    *,
    plane_id: str,
    origin: Vec3 = (0.0, 0.0, 0.0),
    state: PlaneState = PlaneState.CONFIRMED,
    proposed_by: ProposedBy | None = ProposedBy.USER,
    confidence: float = 1.0,
    unit: PlaneUnit = PlaneUnit.UNKNOWN,
    scale: float | None = None,
    owner_component: str | None = None,
    source_revision: str | None = None,
) -> SketchPlane:
    """A principal plane with the conventional frame for ``principal``."""
    p = PrincipalPlane(principal)
    u, v, n = PRINCIPAL_FRAMES[p]
    return SketchPlane(
        plane_id=plane_id,
        owner_component=owner_component,
        kind=PlaneKind.PRINCIPAL,
        principal=p,
        origin=origin,
        u_axis=u,
        v_axis=v,
        normal=n,
        unit=unit,
        scale=scale,
        source_revision=source_revision,
        state=state,
        proposed_by=proposed_by,
        confidence=confidence,
    )


def unassigned_plane(
    *, plane_id: str, owner_component: str | None = None, source_revision: str | None = None
) -> SketchPlane:
    """The plane of a sketch nobody has placed yet (PLN-001)."""
    return SketchPlane(
        plane_id=plane_id,
        owner_component=owner_component,
        source_revision=source_revision,
    )


# ---------------------------------------------------------------------------
# Sketch space and reassignment
# ---------------------------------------------------------------------------

ReassignMode = Literal["preserve_world", "preserve_local", "project_to_target"]


def sketch_to_local(x: float, y: float) -> tuple[float, float]:
    """A sketch-space point (y down) to plane-local ``(u, v)`` (v up)."""
    return (x, -y)


def local_to_sketch(u: float, v: float) -> tuple[float, float]:
    return (u, -v)


def reassign_point(
    x: float, y: float, source: SketchPlane, target: SketchPlane, mode: ReassignMode
) -> tuple[float, float, float]:
    """Where a sketch-space point lands on ``target``: ``(x, y, off_plane)``.

    * ``preserve_local``: the same local coordinates on the new plane (the
      sketch moves with its plane); never off the plane.
    * ``preserve_world``: the same world position, expressed on the new
      plane. ``off_plane`` is how far the point is from the new plane; it is
      only exact when that is zero.
    * ``project_to_target``: the world position projected straight onto the
      new plane; ``off_plane`` is how far each point moved to get there.

    Points and frames share one length unit (the sketch's own, when its
    scale is unknown); nothing is converted.
    """
    if mode == "preserve_local":
        return (x, y, 0.0)
    u, v = sketch_to_local(x, y)
    world = source.to_world(u, v)
    lu, lv, lw = target.to_local(world)
    sx, sy = local_to_sketch(lu, lv)
    return (sx, sy, abs(lw))
