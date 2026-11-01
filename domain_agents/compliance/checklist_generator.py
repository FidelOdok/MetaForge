"""Checklist generator for compliance regimes.

Loads YAML regime definitions and produces deduplicated compliance
checklists for a given set of target markets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from observability.tracing import get_tracer

from .models import (
    PRODUCT_FEATURES,
    ChecklistItem,
    ComplianceChecklist,
    ComplianceRegime,
    EvidenceStatus,
    EvidenceType,
    ExcludedItem,
    ItemEvidence,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("compliance.checklist_generator")

# Map YAML evidence_type strings to EvidenceType enum values
_EVIDENCE_TYPE_MAP: dict[str, EvidenceType] = {
    "test_report": EvidenceType.TEST_REPORT,
    "declaration": EvidenceType.DECLARATION,
    "certificate": EvidenceType.CERTIFICATE,
    "technical_file": EvidenceType.TECHNICAL_FILE,
    "risk_assessment": EvidenceType.RISK_ASSESSMENT,
}


class ChecklistGenerator:
    """Generates compliance checklists from YAML regime definitions.

    Usage::

        gen = ChecklistGenerator()
        gen.load_regimes(Path("domain_agents/compliance/regimes"))
        checklist = gen.generate_checklist(
            project_id="proj-1",
            product_category="consumer_electronics",
            markets=[ComplianceRegime.UKCA, ComplianceRegime.CE],
        )
    """

    def __init__(self) -> None:
        self._regimes: dict[ComplianceRegime, list[ChecklistItem]] = {}

    @property
    def regimes(self) -> dict[ComplianceRegime, list[ChecklistItem]]:
        """Return loaded regimes mapping."""
        return dict(self._regimes)

    def load_regimes(self, regimes_dir: Path) -> dict[ComplianceRegime, list[ChecklistItem]]:
        """Load all YAML regime files from *regimes_dir*.

        Returns the parsed mapping of regime -> items.
        """
        with tracer.start_as_current_span("checklist_generator.load_regimes") as span:
            span.set_attribute("regimes_dir", str(regimes_dir))

            if not regimes_dir.is_dir():
                logger.warning("regimes_dir_not_found", path=str(regimes_dir))
                return {}

            for yaml_path in sorted(regimes_dir.glob("*.yaml")):
                items = self._parse_regime_file(yaml_path)
                if items:
                    regime = items[0].regime
                    self._regimes[regime] = items
                    logger.info(
                        "regime_loaded",
                        regime=regime.value,
                        items=len(items),
                        file=yaml_path.name,
                    )

            span.set_attribute("regimes_loaded", len(self._regimes))
            return dict(self._regimes)

    def generate_checklist(
        self,
        project_id: str,
        product_category: str = "consumer_electronics",
        markets: list[ComplianceRegime] | None = None,
        product_features: list[str] | None = None,
        evidence: dict[str, ItemEvidence] | None = None,
    ) -> ComplianceChecklist:
        """Generate a deduplicated checklist for the given target markets.

        FORGE-553: every item of every regime used to be listed whatever the
        product (a product with no radio got RED, FCC 15C and SAR rows), the
        de-duplication kept whichever market came first, and coverage was
        always 0 because no evidence was read. Now:

        - an item (or its category, or its regime) may ``require`` product
          features; with ``product_features`` stated, items whose features
          the product lacks go to ``excluded_items`` with the reason. Without
          it everything is kept and the conditional ids are listed in
          ``conditional_items``, so the gap is visible rather than assumed;
        - markets are walked in a fixed order, and an item naming a standard
          already listed is folded into it (``also_satisfies``);
        - ``evidence`` (item id -> status) sets each item's status, and the
          coverage is the share of items not ``MISSING``.

        ``product_category`` is a label; applicability comes from features.
        If *markets* is ``None``, all loaded regimes are included.
        """
        with tracer.start_as_current_span("checklist_generator.generate") as span:
            if markets is None:
                markets = list(self._regimes.keys())
            features = set(product_features) if product_features is not None else None
            if features is not None:
                unknown = features - PRODUCT_FEATURES
                if unknown:
                    raise ValueError(
                        f"unknown product feature(s) {sorted(unknown)}; "
                        f"known: {sorted(PRODUCT_FEATURES)}"
                    )
            evidence = evidence or {}

            span.set_attribute("project_id", project_id)
            span.set_attribute("markets", [m.value for m in markets])

            by_standard: dict[str, ChecklistItem] = {}
            items: list[ChecklistItem] = []
            excluded: list[ExcludedItem] = []
            conditional: list[str] = []

            for market in sorted(set(markets), key=lambda m: m.value):
                for template in self._regimes.get(market, []):
                    item = template.model_copy(deep=True)
                    missing = sorted(set(item.requires) - features) if features is not None else []
                    if missing:
                        excluded.append(
                            ExcludedItem(
                                id=item.id,
                                requires=item.requires,
                                reason=f"product does not have: {', '.join(missing)}",
                            )
                        )
                        continue
                    kept = by_standard.get(item.standard)
                    if kept is not None:
                        kept.also_satisfies.append(item.id)
                        logger.debug(
                            "duplicate_standard_folded", standard=item.standard, item_id=item.id
                        )
                        continue
                    if item.requires and features is None:
                        conditional.append(item.id)
                    held = evidence.get(item.id)
                    if held is not None:
                        item.evidence_status = held.status
                        item.evidence_work_product_id = held.work_product_id
                    by_standard[item.standard] = item
                    items.append(item)

            evidenced = sum(1 for i in items if i.evidence_status is not EvidenceStatus.MISSING)
            total = len(items)
            coverage = (evidenced / total * 100.0) if total > 0 else 0.0

            checklist = ComplianceChecklist(
                project_id=project_id,
                product_category=product_category,
                target_markets=markets,
                items=items,
                total_items=total,
                evidenced_items=evidenced,
                coverage_percent=round(coverage, 2),
                product_features=sorted(features) if features is not None else None,
                excluded_items=excluded,
                conditional_items=conditional,
                generated_at=datetime.now(UTC),
            )

            span.set_attribute("total_items", total)
            span.set_attribute("coverage_percent", coverage)
            logger.info(
                "checklist_generated",
                project_id=project_id,
                markets=[m.value for m in markets],
                total_items=total,
                excluded=len(excluded),
                conditional=len(conditional),
            )

            return checklist

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_regime_file(self, yaml_path: Path) -> list[ChecklistItem]:
        """Parse a single YAML regime file into ChecklistItem instances."""
        try:
            data: dict[str, Any] = yaml.safe_load(yaml_path.read_text())
        except Exception as exc:
            logger.error("yaml_parse_error", file=str(yaml_path), error=str(exc))
            return []

        regime_str: str = data.get("regime", "")
        try:
            regime = ComplianceRegime(regime_str)
        except ValueError:
            logger.error("unknown_regime", regime=regime_str, file=str(yaml_path))
            return []

        regime_requires = list(data.get("requires") or [])
        items: list[ChecklistItem] = []
        for category in data.get("categories", []):
            cat_name: str = category.get("name", "unknown")
            cat_requires = regime_requires + list(category.get("requires") or [])
            for raw_item in category.get("items", []):
                ev_type_str: str = raw_item.get("evidence_type", "test_report")
                ev_type = _EVIDENCE_TYPE_MAP.get(ev_type_str, EvidenceType.TEST_REPORT)

                items.append(
                    ChecklistItem(
                        id=raw_item["id"],
                        regime=regime,
                        category=cat_name,
                        requirement=raw_item["requirement"],
                        standard=raw_item["standard"],
                        evidence_type=ev_type,
                        requires=sorted(set(cat_requires) | set(raw_item.get("requires") or [])),
                    )
                )

        return items
