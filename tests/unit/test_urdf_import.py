"""Bring somebody else's robot in as a reference design (FORGE-347).

D4 is "CAD authoring through the Design IR (validated, named parts);
STEP / URDF reference-design import". STEP import worked and preserved
the file's own part labels. URDF had **export only** -- you could write
one out of an assembly, and there was no way to bring an existing arm in
to design against.

A URDF is a rigid-body graph: links are parts, joints are the connections
between them, which is what `twin.commit_system_architecture` already
stores as components and interfaces. These tests mostly pin the honesty
of a partial import: a URDF references meshes by path, usually
`package://` URIs that resolve only inside a ROS workspace, so the
structure comes in and the shapes do not -- and that has to be said.
"""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.twin.urdf import UrdfParseError, parse_urdf

ARM = """
<robot name="two_link_arm">
  <link name="base_link">
    <inertial><mass value="2.5"/></inertial>
    <visual><geometry><mesh filename="package://arm/meshes/base.stl"/></geometry></visual>
  </link>
  <link name="upper_arm"/>
  <link name="forearm"/>
  <joint name="shoulder" type="revolute">
    <parent link="base_link"/>
    <child link="upper_arm"/>
    <axis xyz="0 0 1"/>
  </joint>
  <joint name="elbow" type="continuous">
    <parent link="upper_arm"/>
    <child link="forearm"/>
  </joint>
</robot>
"""


# ---------------------------------------------------------------------------
# The structure
# ---------------------------------------------------------------------------


def test_links_become_components_keeping_their_names() -> None:
    """Named parts, the same rule STEP import follows. A reference design
    full of Link_1..N is not a reference to anything."""
    model = parse_urdf(ARM)
    assert [c["name"] for c in model.components] == ["base_link", "upper_arm", "forearm"]
    assert all(c["discipline"] == "mechanical" for c in model.components)


def test_joints_become_interfaces_carrying_their_type() -> None:
    model = parse_urdf(ARM)
    by_name = {(i["from"], i["to"]): i for i in model.interfaces}
    assert by_name[("base_link", "upper_arm")]["interface_type"] == "mechanical:revolute"
    assert by_name[("upper_arm", "forearm")]["interface_type"] == "mechanical:continuous"


def test_the_axis_is_kept_in_the_description() -> None:
    model = parse_urdf(ARM)
    shoulder = next(i for i in model.interfaces if i["to"] == "upper_arm")
    assert "axis 0 0 1" in shoulder["description"]


def test_mass_is_kept_where_the_file_gave_one() -> None:
    model = parse_urdf(ARM)
    base = next(c for c in model.components if c["name"] == "base_link")
    assert "2.5" in base["description"]


def test_the_robot_name_is_used() -> None:
    assert parse_urdf(ARM).name == "two_link_arm"


# ---------------------------------------------------------------------------
# What did not come in
# ---------------------------------------------------------------------------


def test_meshes_are_named_rather_than_silently_dropped() -> None:
    """A robot imported with no shape, reported as a success, is the
    failure mode this codebase keeps finding."""
    model = parse_urdf(ARM)
    meshes = [n for n in model.not_imported if n["kind"] == "mesh"]
    assert len(meshes) == 1
    assert "package://arm/meshes/base.stl" in meshes[0]["detail"]


def test_an_unknown_joint_type_is_reported_not_guessed() -> None:
    """A joint whose type we invented would move wrongly in every
    downstream simulation."""
    model = parse_urdf(
        """
        <robot name="r">
          <link name="a"/><link name="b"/>
          <joint name="weird" type="screw">
            <parent link="a"/><child link="b"/>
          </joint>
        </robot>
        """
    )
    assert model.interfaces == []
    assert model.not_imported[0]["detail"].startswith("unrecognised joint type")


def test_a_joint_naming_a_missing_link_is_reported() -> None:
    model = parse_urdf(
        """
        <robot name="r">
          <link name="a"/>
          <joint name="dangling" type="fixed">
            <parent link="a"/><child link="ghost"/>
          </joint>
        </robot>
        """
    )
    assert model.interfaces == []
    assert "ghost" in model.not_imported[0]["detail"]


def test_a_clean_file_reports_nothing_skipped() -> None:
    model = parse_urdf(
        """
        <robot name="r">
          <link name="a"/><link name="b"/>
          <joint name="j" type="fixed"><parent link="a"/><child link="b"/></joint>
        </robot>
        """
    )
    assert model.not_imported == []


# ---------------------------------------------------------------------------
# Refusing what is not a URDF
# ---------------------------------------------------------------------------


def test_malformed_xml_is_refused() -> None:
    with pytest.raises(UrdfParseError, match="well-formed"):
        parse_urdf("<robot><link")


def test_a_non_urdf_document_is_refused() -> None:
    with pytest.raises(UrdfParseError, match="expected <robot>"):
        parse_urdf("<sdf version='1.6'><model name='x'/></sdf>")


def test_a_file_with_no_links_is_refused() -> None:
    """An empty import reporting success is worse than an error."""
    with pytest.raises(UrdfParseError, match="nothing to import"):
        parse_urdf("<robot name='empty'/>")


# ---------------------------------------------------------------------------
# It reaches the twin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_tool_records_through_the_existing_architecture_recorder() -> None:
    """Reused, not reinvented: a URDF stored in a shape nothing else reads
    would be an import to nowhere."""
    from tool_registry.tools.twin.adapter import TwinServer

    captured: dict[str, Any] = {}

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"node_id": "wp-9"}

    server = TwinServer(twin=None, system_architecture_recorder=recorder)
    out = await server.import_urdf({"urdf": ARM, "project_id": "p-1"})

    assert out["node_id"] == "wp-9"
    assert out["component_count"] == 3
    assert out["interface_count"] == 2
    assert len(out["not_imported"]) == 1
    assert [c["name"] for c in captured["components"]] == [
        "base_link",
        "upper_arm",
        "forearm",
    ]


@pytest.mark.asyncio
async def test_the_tool_is_registered_when_the_recorder_is() -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        return {}

    server = TwinServer(twin=None, system_architecture_recorder=recorder)
    assert "twin.import_urdf" in server.tool_ids

    without = TwinServer(twin=None)
    assert "twin.import_urdf" not in without.tool_ids


@pytest.mark.asyncio
async def test_a_bad_document_comes_back_as_a_tool_error() -> None:
    from tool_registry.tools.twin.adapter import TwinServer

    async def recorder(**kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("must not record an unparseable file")

    server = TwinServer(twin=None, system_architecture_recorder=recorder)
    with pytest.raises(ValueError, match="twin.import_urdf"):
        await server.import_urdf({"urdf": "<sdf/>"})
