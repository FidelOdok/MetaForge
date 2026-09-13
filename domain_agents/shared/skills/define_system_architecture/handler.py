"""Handler for the define_system_architecture skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import DefineSystemArchitectureInput, DefineSystemArchitectureOutput


class DefineSystemArchitectureHandler(
    SkillBase[DefineSystemArchitectureInput, DefineSystemArchitectureOutput]
):
    """Persists a component/interface map as a SYSTEM_ARCHITECTURE work
    product via twin.commit_system_architecture, which renders the block
    diagram and flags interfaces referencing an undeclared component."""

    input_type = DefineSystemArchitectureInput
    output_type = DefineSystemArchitectureOutput

    async def validate_preconditions(self, input_data: DefineSystemArchitectureInput) -> list[str]:
        errors: list[str] = []
        if not await self.context.mcp.is_available("twin.commit_system_architecture"):
            errors.append("twin.commit_system_architecture tool is not available")
        return errors

    async def execute(
        self, input_data: DefineSystemArchitectureInput
    ) -> DefineSystemArchitectureOutput:
        self.logger.info(
            "Defining system architecture",
            system_name=input_data.system_name,
            component_count=len(input_data.components),
            interface_count=len(input_data.interfaces),
        )
        result = await self.context.mcp.invoke(
            "twin.commit_system_architecture",
            {
                "name": f"{input_data.system_name} Architecture",
                "system_name": input_data.system_name,
                "components": [c.model_dump() for c in input_data.components],
                "interfaces": [i.model_dump(by_alias=True) for i in input_data.interfaces],
                "project_id": input_data.project_id,
            },
        )
        return DefineSystemArchitectureOutput(
            node_id=result["node_id"],
            component_count=int(result.get("component_count", len(input_data.components))),
            interface_count=int(result.get("interface_count", len(input_data.interfaces))),
            dangling_interface_count=len(result.get("dangling_interfaces") or []),
        )
