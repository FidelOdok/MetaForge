"""Handler for the create_procurement_record skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import CreateProcurementRecordInput, CreateProcurementRecordOutput


class CreateProcurementRecordHandler(
    SkillBase[CreateProcurementRecordInput, CreateProcurementRecordOutput]
):
    """Persists a purchase-order-style procurement record as a
    PROCUREMENT_RECORD work product via twin.commit_procurement_record,
    usually linked back to the BOM it was sourced from."""

    input_type = CreateProcurementRecordInput
    output_type = CreateProcurementRecordOutput

    async def validate_preconditions(self, input_data: CreateProcurementRecordInput) -> list[str]:
        errors: list[str] = []
        if input_data.bom_work_product_id is not None:
            work_product = await self.context.twin.get_work_product(
                input_data.bom_work_product_id, branch=self.context.branch
            )
            if work_product is None:
                errors.append(f"WorkProduct {input_data.bom_work_product_id} not found in Twin")
        if not await self.context.mcp.is_available("twin.commit_procurement_record"):
            errors.append("twin.commit_procurement_record tool is not available")
        return errors

    async def execute(
        self, input_data: CreateProcurementRecordInput
    ) -> CreateProcurementRecordOutput:
        self.logger.info(
            "Creating procurement record",
            name=input_data.name,
            line_item_count=len(input_data.line_items),
        )
        source_node_ids = (
            [str(input_data.bom_work_product_id)] if input_data.bom_work_product_id else None
        )
        result = await self.context.mcp.invoke(
            "twin.commit_procurement_record",
            {
                "name": input_data.name,
                "line_items": [li.model_dump() for li in input_data.line_items],
                "notes": input_data.notes,
                "source_node_ids": source_node_ids,
                "project_id": input_data.project_id,
            },
        )
        return CreateProcurementRecordOutput(
            node_id=result["node_id"],
            line_item_count=int(result.get("line_item_count", len(input_data.line_items))),
            total_cost=float(result.get("total_cost", 0.0)),
            currency=str(result.get("currency", "USD")),
            max_lead_time_days=int(result.get("max_lead_time_days", 0)),
        )
