"""Per-product-type requirement completeness checklist (FORGE-257, gap G-A1
-- "complete against a checklist per product type").

Same curated-keyword-list philosophy ``linter.py`` already documents (real
NLU isn't attempted -- a small vetted vocabulary is) applied here at the SET
level: does the product's requirement set, taken as a whole, say anything at
all about each of the checklist's named concern categories? A category with
zero keyword hits across every requirement's text is flagged missing. This
can't judge whether what IS said is any good -- that's the per-requirement
quality checks (``quality.py``) -- only whether the topic was addressed.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_CHECKLIST_DIR = Path(__file__).parent / "checklists"
_DEFAULT_PRODUCT_TYPE = "generic"


class ChecklistCategory(BaseModel):
    id: str
    label: str
    keywords: list[str] = Field(default_factory=list)


class ProductChecklist(BaseModel):
    product_type: str
    categories: list[ChecklistCategory] = Field(default_factory=list)


class CompletenessResult(BaseModel):
    product_type: str
    covered: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def load_checklist(product_type: str) -> ProductChecklist:
    """Load ``<product_type>.yaml`` from the checklists dir, falling back to
    ``generic.yaml`` when no product-specific checklist exists -- an unknown
    product type is never a hard error, since the generic checklist is a
    real, useful floor on its own."""
    slug = (product_type or _DEFAULT_PRODUCT_TYPE).strip().lower().replace(" ", "_")
    path = _CHECKLIST_DIR / f"{slug}.yaml"
    if not path.exists():
        path = _CHECKLIST_DIR / f"{_DEFAULT_PRODUCT_TYPE}.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    categories = [ChecklistCategory(**c) for c in data.get("categories", [])]
    return ProductChecklist(product_type=data.get("product_type", slug), categories=categories)


def check_completeness(
    requirement_texts: list[str], checklist: ProductChecklist
) -> CompletenessResult:
    corpus = " \n".join(t.lower() for t in requirement_texts)
    covered: list[str] = []
    missing: list[str] = []
    for category in checklist.categories:
        if any(kw.lower() in corpus for kw in category.keywords):
            covered.append(category.id)
        else:
            missing.append(category.id)
    return CompletenessResult(product_type=checklist.product_type, covered=covered, missing=missing)
