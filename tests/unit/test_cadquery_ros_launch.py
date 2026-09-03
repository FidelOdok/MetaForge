"""Unit tests for ROS 2 launch file generation (see ros_launch.py's module
docstring -- grounded against ros/urdf_launch's real launch files)."""

from __future__ import annotations

import ast

from tool_registry.tools.cadquery.ros_launch import build_ros2_launch_py


class TestBuildRos2LaunchPy:
    def test_default_variant_is_valid_python(self):
        text = build_ros2_launch_py(robot_name="widget", default_urdf_path="/tmp/widget.urdf")
        ast.parse(text)  # raises SyntaxError if malformed

    def test_default_variant_includes_all_three_nodes(self):
        text = build_ros2_launch_py(robot_name="widget", default_urdf_path="/tmp/widget.urdf")
        assert "robot_state_publisher" in text
        assert "joint_state_publisher" in text
        assert "joint_state_publisher_gui" in text
        assert "rviz2" in text

    def test_minimal_variant_is_valid_python_and_omits_optional_nodes(self):
        text = build_ros2_launch_py(
            robot_name="widget",
            default_urdf_path="/tmp/widget.urdf",
            include_joint_state_publisher_gui=False,
            include_rviz=False,
        )
        ast.parse(text)
        assert "joint_state_publisher_gui" not in text
        assert "rviz2" not in text
        # the non-gui joint_state_publisher node is still present
        assert "joint_state_publisher" in text

    def test_apostrophe_in_path_does_not_break_generated_syntax(self):
        """A naive f-string embed of a caller-provided path (e.g.
        containing an apostrophe) would break the generated file's own
        string literals -- this is exactly the class of bug caught while
        building this (see the commit history / MET-706 session notes).
        Uses repr() to embed the value safely; this locks that in."""
        text = build_ros2_launch_py(
            robot_name="robot's_arm",
            default_urdf_path="/tmp/robot's model.urdf",
        )
        ast.parse(text)

    def test_default_urdf_path_appears_as_the_argument_default(self):
        text = build_ros2_launch_py(robot_name="widget", default_urdf_path="/opt/widget.urdf")
        assert "/opt/widget.urdf" in text
        assert "default_value=" in text

    def test_robot_name_appears_in_docstring(self):
        text = build_ros2_launch_py(robot_name="my_robot", default_urdf_path="/tmp/x.urdf")
        assert "my_robot" in text

    def test_urdf_content_is_read_via_cat_at_launch_time_not_baked_in(self):
        """Matches ros/urdf_launch's own pattern (Command(['xacro ', ...])
        for macro-expansion) -- MetaForge's URDF has no macros, so `cat` is
        the direct equivalent, resolved when the launch file actually
        runs, not when this generator writes it."""
        text = build_ros2_launch_py(robot_name="widget", default_urdf_path="/tmp/widget.urdf")
        assert "Command(['cat " in text
        assert "LaunchConfiguration('urdf_path')" in text
