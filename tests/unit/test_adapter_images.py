"""Compose names the adapter images CI actually publishes (FORGE-587).

`adapter-images.yml` exists for one reason, stated in its own header: building
the freecad and calculix images on the dev host "took the box offline twice",
so CI builds them and the host "then only pulls".

It never pulled. Compose named `metaforge/<adapter>-adapter`, which nothing
publishes, so every host built locally — the exact load the workflow was added
to remove. `docs/mcp_spec.md` had grown a manual `docker tag` step to bridge
the two names, which is the shape of a workaround outliving the bug it hides.

Two invariants, because they fail in opposite directions:

* an adapter CI **publishes** must be named by its published image, or the
  build happens on the host anyway;
* an adapter CI **does not publish** must have a name that cannot resolve to a
  registry at all. `metaforge/<x>` is a real Docker Hub namespace, so a skipped
  build reached out to the internet and 404'd rather than failing locally.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "adapter-images.yml"


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _published_adapters() -> list[str]:
    """The matrix in the workflow, so this test follows CI rather than a copy."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return list(data["jobs"]["publish"]["strategy"]["matrix"]["adapter"])


def _adapter_services() -> dict:
    return {n: s for n, s in _services().items() if n.endswith("-adapter")}


class TestPublishedAdaptersArePulled:
    def test_the_workflow_still_publishes_something(self) -> None:
        assert _published_adapters(), "adapter-images.yml publishes no adapters"

    def test_each_published_adapter_is_named_by_its_ghcr_image(self) -> None:
        services = _services()
        for adapter in _published_adapters():
            svc = services.get(f"{adapter}-adapter")
            assert svc, f"no compose service for published adapter {adapter}"
            image = svc.get("image", "")
            expected = f"ghcr.io/fidelodok/metaforge-{adapter}-adapter"
            assert image.startswith(expected), (
                f"{adapter}-adapter is {image!r}, not the image CI publishes "
                f"({expected}) -- the host will build it instead of pulling"
            )

    def test_a_build_fallback_remains_for_contributors(self) -> None:
        """Pulling must not stop `docker compose up --build` from working."""
        services = _services()
        for adapter in _published_adapters():
            assert services[f"{adapter}-adapter"].get("build"), (
                f"{adapter}-adapter lost its build: section"
            )


class TestUnpublishedAdaptersCannotReachARegistry:
    def test_they_use_a_single_segment_local_name(self) -> None:
        published = set(_published_adapters())
        for name, svc in _adapter_services().items():
            adapter = name.removesuffix("-adapter")
            if adapter in published:
                continue
            image = svc.get("image", "")
            assert "/" not in image, (
                f"{name} is {image!r}: a name with a slash resolves against a "
                "registry, so a skipped build fetches from the internet"
            )

    def test_nothing_names_the_empty_docker_hub_namespace(self) -> None:
        """`metaforge/*` on Docker Hub is a real namespace with no repositories.
        Naming it means a missing build 404s against the internet instead of
        failing locally."""
        text = COMPOSE.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not re.search(r"image:\s*metaforge/", stripped), (
                f"compose names the empty public namespace: {stripped!r}"
            )


class TestEveryHostImageIsPublished:
    """FORGE-593: a host runs with no checkout, so nothing it runs is built there."""

    def _service_images(self) -> dict[str, str]:
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        include = data["jobs"]["publish-services"]["strategy"]["matrix"]["include"]
        return {row["image"]: row["context"] for row in include}

    def test_every_tool_adapter_is_published(self) -> None:
        published = set(_published_adapters())
        unpublished = [
            name for name in _adapter_services() if name.removesuffix("-adapter") not in published
        ]
        assert not unpublished, f"adapters a host would have to build: {unpublished}"

    @pytest.mark.parametrize(
        ("service", "image"),
        [("occt-converter", "metaforge-occt"), ("dashboard", "metaforge-dashboard")],
    )
    def test_the_other_services_are_named_by_their_ghcr_image(
        self, service: str, image: str
    ) -> None:
        assert image in self._service_images(), f"{image} is not published"
        svc = _services()[service]
        assert svc.get("image", "").startswith(f"ghcr.io/fidelodok/{image}:"), svc.get("image")
        assert svc.get("build"), f"{service} lost its build: fallback"
