"""The SimReady Central catalogue, as the index repository lists it (FORGE-711).

``NVIDIA-Omniverse/simready-central`` keeps one file per dataset under
``submissions/<namespace>/<dataset>``, each line the commit-pinned URL of one
package. That list is the curated catalogue (only reviewed packages are in
it), and it already pins every package, so search reads it rather than
crawling Hugging Face.

Licences come from each dataset's Hugging Face card. A search result's
licence is the dataset's; the import checks the package's own statements
too, so a search result is a guide, not a clearance.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import structlog

from tool_registry.tools.simready.config import SimReadyConfig
from tool_registry.tools.simready.package import (
    Fetcher,
    PackageError,
    PackageRef,
    parse_package_url,
)
from twin_core.library.licences import is_accepted, normalise_licence

logger = structlog.get_logger(__name__)

_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def words(text: str) -> list[str]:
    """``airSystem_compressor_a01`` -> ``['air', 'system', 'compressor', 'a', '01']``."""
    return [w.lower() for w in _WORDS.findall(text or "")]


@dataclass(frozen=True)
class IndexEntry:
    ref: PackageRef

    def haystack(self) -> set[str]:
        return {
            *words(self.ref.asset),
            *words(self.ref.dataset),
            *words(self.ref.namespace),
            self.ref.asset.lower(),
            self.ref.namespace.lower(),
        }


class SimReadyIndex:
    """The catalogue and dataset licences, fetched once per ``index_ttl_s``."""

    def __init__(self, config: SimReadyConfig, fetcher: Fetcher) -> None:
        self._config = config
        self._fetcher = fetcher
        self._entries: list[IndexEntry] = []
        self._entries_at = 0.0
        self._licences: dict[str, tuple[float, str | None]] = {}

    def _fresh(self, at: float) -> bool:
        return at > 0 and time.monotonic() - at < self._config.index_ttl_s

    async def entries(self) -> list[IndexEntry]:
        if self._entries and self._fresh(self._entries_at):
            return self._entries
        cfg = self._config
        tree_url = f"{cfg.github_api}/repos/{cfg.index_repo}/git/trees/{cfg.index_ref}?recursive=1"
        tree = json.loads(await self._fetcher.get(tree_url))
        files = [
            t["path"]
            for t in tree.get("tree") or []
            if t.get("type") == "blob"
            and str(t.get("path", "")).startswith("submissions/")
            and str(t["path"]).count("/") == 2
        ]
        latest: dict[tuple[str, str], IndexEntry] = {}
        for path in files:
            raw = f"{cfg.github_raw}/{cfg.index_repo}/{cfg.index_ref}/{path}"
            text = (await self._fetcher.get(raw)).decode("utf-8", "replace")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    ref = parse_package_url(line)
                except PackageError:
                    logger.warning("simready_index_line_skipped", file=path, line=line[:120])
                    continue
                # A later line for the same asset is a newer version of it.
                latest[(ref.dataset_id, ref.asset)] = IndexEntry(ref)
        self._entries = list(latest.values())
        self._entries_at = time.monotonic()
        logger.info("simready_index_loaded", files=len(files), packages=len(self._entries))
        return self._entries

    async def dataset_licence(self, dataset_id: str) -> str | None:
        cached = self._licences.get(dataset_id)
        if cached and self._fresh(cached[0]):
            return cached[1]
        try:
            card = json.loads(await self._fetcher.get(f"{self._config.hf_api}/{dataset_id}"))
        except PackageError as exc:
            logger.warning(
                "simready_dataset_licence_unavailable", dataset=dataset_id, error=str(exc)
            )
            return None
        licence = (card.get("cardData") or {}).get("license")
        if not licence:
            licence = next(
                (t.split(":", 1)[1] for t in card.get("tags") or [] if t.startswith("license:")),
                None,
            )
        if isinstance(licence, list):
            licence = licence[0] if licence else None
        result = normalise_licence(licence)
        self._licences[dataset_id] = (time.monotonic(), result)
        return result

    async def search(
        self,
        query: str,
        *,
        owner: str | None = None,
        limit: int = 20,
        include_unaccepted: bool = False,
    ) -> dict[str, Any]:
        terms = [t for t in words(query) or [query.lower().strip()] if t]
        scored = []
        for entry in await self.entries():
            if owner and entry.ref.namespace.lower() != owner.lower():
                continue
            hay = entry.haystack()
            # Every word must match (a whole word, the start of one, or part of
            # the asset name); whole-word matches rank first.
            if not all(t in hay or any(h.startswith(t) for h in hay) for t in terms):
                continue
            exact = sum(1 for t in terms if t in hay)
            scored.append((exact, entry))
        scored.sort(key=lambda s: (-s[0], s[1].ref.asset))

        results: list[dict[str, Any]] = []
        refused = 0
        for _, entry in scored:
            licence = await self.dataset_licence(entry.ref.dataset_id)
            accepted = is_accepted(licence)
            if not accepted and not include_unaccepted:
                refused += 1
                continue
            results.append(
                {
                    "asset": entry.ref.asset,
                    "dataset": entry.ref.dataset_id,
                    "owner": entry.ref.namespace,
                    "package_url": entry.ref.url,
                    "commit": entry.ref.commit,
                    "licence": licence,
                    "licence_accepted": accepted,
                    "browse_url": entry.ref.browse_url,
                }
            )
            if len(results) >= limit:
                break
        return {
            "query": query,
            "results": results,
            "matched": len(scored),
            "left_out_for_licence": refused,
        }
