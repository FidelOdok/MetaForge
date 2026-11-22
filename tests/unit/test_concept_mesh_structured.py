"""A structured concept mesh clears the handover's concept assembly.

The handover asked for a person to have seen the form, or an illustrative
concept mesh, and only the paid image-to-3D provider makes an illustrative
one. A project with no provider key could never hand over without a person,
although ``twin.render_structured_mesh`` builds a GLB of the same concept for
free: one node per real part at its read size. Either authority now counts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.imagine.twin_bridge import TwinBridge


class _Twin:
    def __init__(self, products: list[Any]) -> None:
        self.products = products

    async def list_work_products(self, project_id: Any = None) -> list[Any]:
        return self.products


def _glb(authority: str, fmt: str = "glb") -> Any:
    return SimpleNamespace(format=fmt, metadata={"authority": authority})


@pytest.mark.parametrize("authority", ["illustrative", "structured"])
async def test_an_illustrative_or_structured_glb_counts(authority: str) -> None:
    bridge = TwinBridge(_Twin([_glb(authority)]))
    assert await bridge.has_concept_mesh(str(uuid4())) is True


@pytest.mark.parametrize(
    "product", [_glb("evidence"), _glb("structured", fmt="step"), _glb("illustrative", fmt="png")]
)
async def test_another_authority_or_format_does_not(product: Any) -> None:
    bridge = TwinBridge(_Twin([product]))
    assert await bridge.has_concept_mesh(str(uuid4())) is False
