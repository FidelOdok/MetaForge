"""Read a URDF into the shape the twin already understands (FORGE-347).

D4 is "CAD authoring through the Design IR (validated, named parts);
**STEP / URDF reference-design import**". STEP import works and preserves
the file's own part labels. URDF had export only -- you could write one
out of an assembly, and there was no way to bring somebody else's arm in
as a reference to design against.

A URDF is a rigid-body graph: links are the parts, joints are the
connections between them. That is the same thing
``twin.commit_system_architecture`` already stores as components and
interfaces, so this parses into that shape rather than adding a
representation nothing else reads.

**Geometry is not imported.** A URDF references meshes by path, usually
``package://`` URIs that only resolve inside a ROS workspace this server
does not have. Bringing in the structure and saying plainly that the
meshes were left behind is more useful than either failing outright or
silently producing a robot with no shape -- so every skipped mesh is
listed in ``not_imported``.

Layer-1 in spirit: stdlib only (``xml.etree``), no I/O, no twin access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree

__all__ = [
    "UrdfModel",
    "UrdfParseError",
    "parse_urdf",
]

#: Joint types the URDF spec defines. Anything else is reported rather
#: than mapped to a guess -- a joint whose type we invented would move
#: wrongly in every downstream simulation.
_KNOWN_JOINT_TYPES = frozenset(
    {"revolute", "continuous", "prismatic", "fixed", "floating", "planar"}
)


class UrdfParseError(ValueError):
    """The document is not a URDF this can read."""


@dataclass
class UrdfModel:
    """Links, joints, and an honest account of what was left behind."""

    name: str
    components: list[dict[str, str]] = field(default_factory=list)
    interfaces: list[dict[str, str]] = field(default_factory=list)
    not_imported: list[dict[str, str]] = field(default_factory=list)
    """Each entry is ``{kind, name, detail}``: meshes whose files were not
    resolved, joints of an unrecognised type, and joints naming a link
    that is not in the file."""


def _text_of(element: ElementTree.Element | None, attr: str) -> str:
    return "" if element is None else str(element.get(attr) or "")


def parse_urdf(source: str) -> UrdfModel:
    """Parse URDF XML into components/interfaces.

    Raises :class:`UrdfParseError` for anything that is not a URDF -- an
    unparseable document, or a well-formed one whose root is not
    ``<robot>``. A file with no links raises too: an empty import that
    reports success is the failure this whole codebase keeps finding.
    """
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as exc:
        raise UrdfParseError(f"not well-formed XML: {exc}") from exc
    if root.tag != "robot":
        raise UrdfParseError(f"root element is <{root.tag}>, expected <robot>")

    model = UrdfModel(name=str(root.get("name") or "imported_robot"))

    link_names: set[str] = set()
    for link in root.findall("link"):
        name = str(link.get("name") or "").strip()
        if not name:
            # A URDF link without a name is malformed; the rest of the file
            # may still be useful, so this is reported, not fatal.
            model.not_imported.append(
                {"kind": "link", "name": "", "detail": "link element has no name attribute"}
            )
            continue
        link_names.add(name)
        inertial = link.find("inertial/mass")
        mass = _text_of(inertial, "value")
        description = f"URDF link. Mass {mass} kg." if mass else "URDF link."
        model.components.append(
            {"name": name, "discipline": "mechanical", "description": description}
        )
        for tag in ("visual", "collision"):
            for mesh in link.findall(f"{tag}/geometry/mesh"):
                filename = str(mesh.get("filename") or "")
                if filename:
                    model.not_imported.append(
                        {
                            "kind": "mesh",
                            "name": f"{name}/{tag}",
                            "detail": (
                                f"{filename} -- geometry is referenced by path and was "
                                "not resolved; the structure was imported without it"
                            ),
                        }
                    )

    if not model.components:
        raise UrdfParseError("no <link> elements: nothing to import")

    for joint in root.findall("joint"):
        name = str(joint.get("name") or "").strip() or "unnamed_joint"
        joint_type = str(joint.get("type") or "").strip()
        parent = _text_of(joint.find("parent"), "link")
        child = _text_of(joint.find("child"), "link")

        if joint_type not in _KNOWN_JOINT_TYPES:
            model.not_imported.append(
                {
                    "kind": "joint",
                    "name": name,
                    "detail": f"unrecognised joint type {joint_type!r}",
                }
            )
            continue
        missing = [n for n in (parent, child) if n and n not in link_names]
        if not parent or not child or missing:
            model.not_imported.append(
                {
                    "kind": "joint",
                    "name": name,
                    "detail": (
                        f"names a link that is not in this file: {', '.join(missing)}"
                        if missing
                        else "missing a <parent> or <child> link"
                    ),
                }
            )
            continue
        axis = _text_of(joint.find("axis"), "xyz")
        detail = f"URDF {joint_type} joint"
        if axis:
            detail += f", axis {axis}"
        model.interfaces.append(
            {
                "from": parent,
                "to": child,
                "interface_type": f"mechanical:{joint_type}",
                "description": detail,
            }
        )

    return model
