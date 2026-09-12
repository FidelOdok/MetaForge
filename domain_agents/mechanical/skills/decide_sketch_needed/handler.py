"""Handler for the decide_sketch_needed skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import DecideSketchNeededInput, DecideSketchNeededOutput


class DecideSketchNeededHandler(SkillBase[DecideSketchNeededInput, DecideSketchNeededOutput]):
    """Pure decision skill -- no MCP tools, no Twin reads. Evaluates a fixed
    set of deterministic rules against the proposed action's shape and
    returns whether a design_sketch gate applies, and why.
    """

    input_type = DecideSketchNeededInput
    output_type = DecideSketchNeededOutput

    # Assemblies at or above this many parts get flagged even without joints --
    # layout/interference risk grows with part count independent of kinematics.
    ASSEMBLY_COMPLEXITY_THRESHOLD = 4

    async def execute(self, input_data: DecideSketchNeededInput) -> DecideSketchNeededOutput:
        reasons: list[str] = []
        is_revision = bool(input_data.source_node_ids)

        if input_data.user_requested_review:
            reasons.append("a human explicitly requested a sketch/review before this work")

        if is_revision:
            reasons.append(
                f"this revises {len(input_data.source_node_ids)} already-built work "
                "product(s) -- changing committed geometry needs an approved reference first"
            )

        if input_data.part_count > 1 and input_data.has_moving_joints:
            reasons.append(
                "multi-body assembly with moving joints -- topology and proportions "
                "must be reviewed before CAD authoring (the failure class behind the "
                "original quadruped-that-didn't-look-like-a-quadruped defect)"
            )

        if input_data.topology_is_novel:
            reasons.append(
                "no prior built reference exists for this shape/mechanism in this project"
            )

        if input_data.part_count >= self.ASSEMBLY_COMPLEXITY_THRESHOLD:
            reasons.append(
                f"assembly complexity ({input_data.part_count} parts) warrants a "
                "layout/proportions check"
            )

        sketch_needed = bool(reasons)
        if not sketch_needed:
            reasons.append(
                "simple, brand-new, low-part-count design with no moving joints and no "
                "novel topology -- proceed directly to CAD authoring"
            )

        self.logger.info(
            "decide_sketch_needed",
            sketch_needed=sketch_needed,
            is_revision=is_revision,
            part_count=input_data.part_count,
            has_moving_joints=input_data.has_moving_joints,
        )

        return DecideSketchNeededOutput(
            sketch_needed=sketch_needed,
            reasons=reasons,
            is_revision=is_revision,
            recommended_source_node_ids=input_data.source_node_ids if sketch_needed else [],
        )
