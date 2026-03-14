"""In-memory evidence tracker for compliance items.

Manages the lifecycle of compliance evidence records: linking,
retrieval, status updates, and coverage reporting.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer

from .models import (
    ChecklistItem,
    ComplianceChecklist,
    ComplianceEvidence,
    EvidenceStatus,
    EvidenceType,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("compliance.evidence_tracker")


class EvidenceTracker:
    """In-memory store for compliance evidence records.

    Usage::

        tracker = EvidenceTracker()
        evidence = tracker.link_evidence(
            checklist_item_id="UKCA-SAF-001",
            evidence_type=EvidenceType.TEST_REPORT,
            title="EN 62368-1 Test Report",
        )
        coverage = tracker.get_coverage(checklist)
    """

    def __init__(self) -> None:
        self._evidence: dict[UUID, ComplianceEvidence] = {}
        # Index: checklist_item_id -> list of evidence UUIDs
        self._item_index: dict[str, list[UUID]] = {}

    def link_evidence(
        self,
        checklist_item_id: str,
        evidence_type: EvidenceType,
        title: str,
        description: str = "",
        work_product_id: UUID | None = None,
    ) -> ComplianceEvidence:
        """Create and store a new evidence record linked to a checklist item.

        Returns the created ComplianceEvidence.
        """
        with tracer.start_as_current_span("evidence_tracker.link") as span:
            span.set_attribute("checklist_item_id", checklist_item_id)
            span.set_attribute("evidence_type", evidence_type.value)

            evidence = ComplianceEvidence(
                id=uuid4(),
                checklist_item_id=checklist_item_id,
                evidence_type=evidence_type,
                status=EvidenceStatus.UPLOADED,
                title=title,
                description=description,
                work_product_id=work_product_id,
            )

            self._evidence[evidence.id] = evidence
            self._item_index.setdefault(checklist_item_id, []).append(evidence.id)

            logger.info(
                "evidence_linked",
                evidence_id=str(evidence.id),
                checklist_item_id=checklist_item_id,
                title=title,
            )

            return evidence

    def get_evidence(self, evidence_id: UUID) -> ComplianceEvidence | None:
        """Retrieve an evidence record by UUID."""
        return self._evidence.get(evidence_id)

    def get_evidence_for_item(self, checklist_item_id: str) -> list[ComplianceEvidence]:
        """Return all evidence records linked to a checklist item."""
        ids = self._item_index.get(checklist_item_id, [])
        return [self._evidence[eid] for eid in ids if eid in self._evidence]

    def update_status(
        self,
        evidence_id: UUID,
        status: EvidenceStatus,
        reviewed_by: str | None = None,
        approved_by: str | None = None,
    ) -> ComplianceEvidence | None:
        """Update the status of an evidence record.

        Returns the updated record, or None if not found.
        """
        with tracer.start_as_current_span("evidence_tracker.update_status") as span:
            span.set_attribute("evidence_id", str(evidence_id))
            span.set_attribute("new_status", status.value)

            evidence = self._evidence.get(evidence_id)
            if evidence is None:
                logger.warning("evidence_not_found", evidence_id=str(evidence_id))
                return None

            evidence.status = status
            if reviewed_by is not None:
                evidence.reviewed_by = reviewed_by
            if approved_by is not None:
                evidence.approved_by = approved_by

            logger.info(
                "evidence_status_updated",
                evidence_id=str(evidence_id),
                status=status.value,
            )

            return evidence

    def get_coverage(self, checklist: ComplianceChecklist) -> dict[str, float | int]:
        """Compute evidence coverage statistics for a checklist.

        Returns a dict with:
        - total_items: number of checklist items
        - evidenced_items: items with at least one non-MISSING evidence
        - coverage_percent: 0-100 coverage ratio
        """
        with tracer.start_as_current_span("evidence_tracker.get_coverage") as span:
            total = len(checklist.items)
            evidenced = 0

            for item in checklist.items:
                records = self.get_evidence_for_item(item.id)
                if any(r.status != EvidenceStatus.MISSING for r in records):
                    evidenced += 1

            coverage = (evidenced / total * 100.0) if total > 0 else 0.0

            span.set_attribute("total_items", total)
            span.set_attribute("evidenced_items", evidenced)
            span.set_attribute("coverage_percent", coverage)

            return {
                "total_items": total,
                "evidenced_items": evidenced,
                "coverage_percent": round(coverage, 2),
            }

    def get_missing_items(self, checklist: ComplianceChecklist) -> list[ChecklistItem]:
        """Return checklist items that have no evidence linked."""
        missing: list[ChecklistItem] = []
        for item in checklist.items:
            records = self.get_evidence_for_item(item.id)
            if not records:
                missing.append(item)
        return missing
