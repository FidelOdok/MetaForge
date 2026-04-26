"""Cross-reference identity resolution across context fragments (MET-324).

Same physical component shows up under different identities depending on
the source: schematic uses the ref-designator (``R12``), the BOM uses the
manufacturer part number (``ERJ-3EKF1002V``), CAD references a
work-product UUID, the datasheet is keyed by file path. Without a
resolver, the agent treats each citation as a fresh entity and can ship
contradictory designs without ever realising the BOM and the schematic
disagree.

``IdentityResolver`` builds a small in-memory union-find graph keyed by
*identity tokens* extracted from each fragment, then exposes:

* ``resolve(fragments)`` → annotates each fragment with a stable
  ``resolved_identity`` (the cluster's canonical token) in
  ``fragment.metadata["resolved_identity"]``.
* ``orphans(fragments)`` → fragments whose only token is unique (i.e.
  no other fragment references them) — these are the BOM-without-
  schematic style misses called out in MET-324's spec.
* ``mismatches(fragments)`` → ``IdentityMismatch`` rows where two
  fragments share a *weak* token (ref-des or part_class) but disagree
  on a *strong* one (MPN or work_product_id). These are the
  R12/R13-in-BOM style errors and become ``BLOCKING`` conflicts via
  ``ConflictDetector``.

Token strength order (strong → weak):

1. ``work_product_id`` — UUID, globally unique.
2. ``mpn`` — manufacturer part number, globally unique per supplier.
3. ``ref_des`` — board-local reference designator (R12, U3, J1).
4. ``part_class`` — coarse category (resistor, mcu, connector).

Strong tokens are *equivalence anchors*: two fragments sharing one are
the same component. Weak tokens are *suggestion anchors*: two fragments
sharing one are *probably* the same — but only if their strong tokens
agree (or one side has none). A weak match with disagreeing strong
tokens is a mismatch.

The resolver does **not** mutate the Twin graph itself — that's the
caller's job. We only annotate fragments and return diagnostic rows;
``ContextAssembler`` wires the rest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from observability.tracing import get_tracer

if TYPE_CHECKING:
    from digital_twin.context.models import ContextFragment

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.context.identity_resolver")

__all__ = [
    "IdentityCluster",
    "IdentityMismatch",
    "IdentityResolver",
    "IdentityTokens",
    "STRONG_FIELDS",
    "WEAK_FIELDS",
]


STRONG_FIELDS: tuple[str, ...] = ("work_product_id", "mpn")
"""Globally-unique identity fields. Two fragments sharing one are linked."""

WEAK_FIELDS: tuple[str, ...] = ("ref_des", "part_class")
"""Locally-unique fields. Two fragments sharing one *suggest* a link."""


# Ref-designators look like "R12", "U3", "J1A", "C100" — letter prefix
# followed by digits, optionally a trailing letter. Match in both
# metadata and content.
_REF_DES_RE = re.compile(r"\b([A-Z]{1,3}\d{1,4}[A-Z]?)\b")

# MPNs are vendor-specific but typically have ≥2 dashes/dots and uppercase
# letters. Conservative regex to cut false positives.
_MPN_RE = re.compile(r"\b((?:[A-Z][A-Z0-9]{1,4}[-./]){2,}[A-Z0-9]{1,8})\b")


@dataclass
class IdentityTokens:
    """Identity signals lifted off one fragment."""

    fragment_index: int
    work_product_id: str | None = None
    mpn: str | None = None
    ref_des: str | None = None
    part_class: str | None = None

    def strong_tokens(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.work_product_id:
            out["work_product_id"] = self.work_product_id
        if self.mpn:
            out["mpn"] = self.mpn
        return out

    def weak_tokens(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.ref_des:
            out["ref_des"] = self.ref_des
        if self.part_class:
            out["part_class"] = self.part_class
        return out

    def any(self) -> bool:
        return bool(self.work_product_id or self.mpn or self.ref_des or self.part_class)


@dataclass
class IdentityCluster:
    """One resolved identity, plus the fragments that share it."""

    canonical: str
    fragment_indices: list[int] = field(default_factory=list)
    member_tokens: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class IdentityMismatch:
    """Two fragments suggested-equal by weak tokens but disagree on a strong one.

    ``ConflictDetector`` consumes this list to emit ``BLOCKING``
    ``Conflict`` rows; agents treat ``BLOCKING`` mismatches as a refuse-
    to-act signal.
    """

    field: str
    weak_field: str
    weak_value: str
    fragment_index_a: int
    fragment_index_b: int
    source_id_a: str
    source_id_b: str
    value_a: str
    value_b: str

    @property
    def description(self) -> str:
        return (
            f"{self.weak_field}={self.weak_value!r} appears in two "
            f"sources with different {self.field} ({self.value_a!r} vs "
            f"{self.value_b!r})"
        )


class _UnionFind:
    """Tiny path-compressed union-find — small N, no need for ranks."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self._parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        cur = x
        while self._parent[cur] != root:
            nxt = self._parent[cur]
            self._parent[cur] = root
            cur = nxt
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for node in list(self._parent):
            out.setdefault(self.find(node), []).append(node)
        return out


class IdentityResolver:
    """Build identity clusters across a list of context fragments."""

    def __init__(
        self,
        strong_fields: tuple[str, ...] = STRONG_FIELDS,
        weak_fields: tuple[str, ...] = WEAK_FIELDS,
    ) -> None:
        self._strong_fields = strong_fields
        self._weak_fields = weak_fields

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, fragments: list[ContextFragment]) -> list[IdentityCluster]:
        """Build identity clusters and annotate each fragment in-place.

        Each fragment with at least one identity token gets
        ``metadata["resolved_identity"]`` set to its cluster's canonical
        token (preferring the strongest field available cluster-wide).

        Fragments with no extractable tokens are left untouched and
        excluded from the returned clusters.
        """
        with tracer.start_as_current_span("identity.resolve") as span:
            tokens = [self._extract(idx, f) for idx, f in enumerate(fragments)]
            span.set_attribute("identity.fragment_count", len(fragments))

            uf = _UnionFind()
            # 1. Add a node per (field, value) pair.
            for tk in tokens:
                for v in tk.strong_tokens().values():
                    uf.add(self._key("strong", v))
                for v in tk.weak_tokens().values():
                    uf.add(self._key("weak", v))

            # 2. Union all tokens belonging to one fragment together.
            for tk in tokens:
                anchors = [
                    *(self._key("strong", v) for v in tk.strong_tokens().values()),
                    *(self._key("weak", v) for v in tk.weak_tokens().values()),
                ]
                for other in anchors[1:]:
                    uf.union(anchors[0], other)

            # 3. Materialise clusters.
            cluster_map: dict[str, IdentityCluster] = {}
            for tk in tokens:
                anchor = self._cluster_anchor(tk, uf)
                if anchor is None:
                    continue
                cluster = cluster_map.setdefault(
                    anchor, IdentityCluster(canonical=self._strip_kind(anchor))
                )
                cluster.fragment_indices.append(tk.fragment_index)
                for fld, val in {**tk.strong_tokens(), **tk.weak_tokens()}.items():
                    cluster.member_tokens.setdefault(fld, set()).add(val)

            # 4. Promote canonical to the strongest token in the cluster
            # (union-find may have arbitrarily rooted on a weak one).
            for cluster in cluster_map.values():
                for fld in (*self._strong_fields, *self._weak_fields):
                    values = cluster.member_tokens.get(fld)
                    if values:
                        cluster.canonical = sorted(values)[0]
                        break

            # 5. Stamp resolved_identity on each fragment.
            for cluster in cluster_map.values():
                for idx in cluster.fragment_indices:
                    fragments[idx].metadata["resolved_identity"] = cluster.canonical

            span.set_attribute("identity.cluster_count", len(cluster_map))
            logger.info(
                "identity_resolved",
                clusters=len(cluster_map),
                fragments=len(fragments),
            )
            return list(cluster_map.values())

    def orphans(self, fragments: list[ContextFragment]) -> list[ContextFragment]:
        """Return fragments whose identity cluster has only one member.

        A BOM entry that nothing else references, a schematic component
        nobody buys — both surface here.
        """
        clusters = self.resolve(fragments)
        cluster_size: dict[str, int] = {c.canonical: len(c.fragment_indices) for c in clusters}
        out: list[ContextFragment] = []
        for fragment in fragments:
            cid = fragment.metadata.get("resolved_identity")
            if isinstance(cid, str) and cluster_size.get(cid, 0) == 1:
                out.append(fragment)
        return out

    def mismatches(self, fragments: list[ContextFragment]) -> list[IdentityMismatch]:
        """Find fragments linked by a weak token but disagreeing on a strong one.

        Spec example: schematic shows ``R12`` with MPN ``ERJ-3EKF1002V``
        but the BOM lists the same ref-des with ``RC0603FR-071K``.
        """
        tokens = [self._extract(idx, f) for idx, f in enumerate(fragments)]
        # Map weak token → list of (token_index, fragment).
        weak_groups: dict[tuple[str, str], list[tuple[int, ContextFragment]]] = {}
        for tk in tokens:
            for fld, val in tk.weak_tokens().items():
                weak_groups.setdefault((fld, val), []).append(
                    (tk.fragment_index, fragments[tk.fragment_index])
                )

        out: list[IdentityMismatch] = []
        seen: set[tuple[int, int, str]] = set()
        for (weak_field, weak_value), members in weak_groups.items():
            if len(members) < 2:
                continue
            for i in range(len(members)):
                idx_a, frag_a = members[i]
                tok_a = tokens[idx_a]
                for idx_b, frag_b in members[i + 1 :]:
                    tok_b = tokens[idx_b]
                    for strong in self._strong_fields:
                        a = tok_a.strong_tokens().get(strong)
                        b = tok_b.strong_tokens().get(strong)
                        if a and b and a != b:
                            key = (
                                min(idx_a, idx_b),
                                max(idx_a, idx_b),
                                strong,
                            )
                            if key in seen:
                                continue
                            seen.add(key)
                            out.append(
                                IdentityMismatch(
                                    field=strong,
                                    weak_field=weak_field,
                                    weak_value=weak_value,
                                    fragment_index_a=idx_a,
                                    fragment_index_b=idx_b,
                                    source_id_a=frag_a.source_id,
                                    source_id_b=frag_b.source_id,
                                    value_a=a,
                                    value_b=b,
                                )
                            )
        return out

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    def _extract(self, fragment_index: int, fragment: ContextFragment) -> IdentityTokens:
        tokens = IdentityTokens(fragment_index=fragment_index)
        meta = fragment.metadata or {}

        # Strong: explicit fields win.
        wp = meta.get("work_product_id") or fragment.work_product_id
        if wp:
            tokens.work_product_id = str(wp)
        mpn_meta = meta.get("mpn")
        if isinstance(mpn_meta, str | int | float) and str(mpn_meta).strip():
            tokens.mpn = str(mpn_meta).strip()

        # Weak: explicit metadata overrides regex.
        ref_meta = meta.get("ref_des") or meta.get("ref_designator")
        if isinstance(ref_meta, str | int | float) and str(ref_meta).strip():
            tokens.ref_des = str(ref_meta).strip().upper()
        part_class = meta.get("part_class") or meta.get("category")
        if isinstance(part_class, str) and part_class.strip():
            tokens.part_class = part_class.strip().lower()

        # Content scan only for unset fields.
        if fragment.content:
            if tokens.mpn is None:
                m = _MPN_RE.search(fragment.content)
                if m:
                    tokens.mpn = m.group(1)
            if tokens.ref_des is None:
                m = _REF_DES_RE.search(fragment.content)
                if m:
                    tokens.ref_des = m.group(1).upper()
        return tokens

    # ------------------------------------------------------------------
    # Cluster anchoring
    # ------------------------------------------------------------------

    def _cluster_anchor(self, tk: IdentityTokens, uf: _UnionFind) -> str | None:
        """Pick the strongest token from this fragment as the cluster key.

        Returns the union-find root of that token so all fragments
        sharing the cluster receive the same canonical id.
        """
        for fld in self._strong_fields:
            v = tk.strong_tokens().get(fld)
            if v:
                return uf.find(self._key("strong", v))
        for fld in self._weak_fields:
            v = tk.weak_tokens().get(fld)
            if v:
                return uf.find(self._key("weak", v))
        return None

    @staticmethod
    def _key(kind: str, value: str) -> str:
        return f"{kind}:{value}"

    @staticmethod
    def _strip_kind(key: str) -> str:
        if ":" in key:
            return key.split(":", 1)[1]
        return key

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def annotate_iter(self, fragments: Iterable[ContextFragment]) -> list[ContextFragment]:
        """Resolve + return the same list (handy for chaining)."""
        listed = list(fragments)
        self.resolve(listed)
        return listed
