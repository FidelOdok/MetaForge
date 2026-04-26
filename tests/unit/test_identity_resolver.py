"""Unit tests for ``digital_twin.context.identity_resolver`` (MET-324)."""

from __future__ import annotations

from uuid import uuid4

from digital_twin.context.identity_resolver import IdentityResolver
from digital_twin.context.models import ContextFragment, ContextSourceKind


def _frag(
    *,
    source_id: str,
    content: str = "",
    metadata: dict[str, object] | None = None,
) -> ContextFragment:
    return ContextFragment(
        content=content or "(none)",
        source_kind=ContextSourceKind.KNOWLEDGE_HIT,
        source_id=source_id,
        metadata=dict(metadata or {}),
        token_count=10,
    )


# ---------------------------------------------------------------------------
# resolve / clustering
# ---------------------------------------------------------------------------


class TestResolve:
    def test_two_fragments_share_mpn_collapse_into_one_cluster(self) -> None:
        a = _frag(source_id="schematic://U1", metadata={"mpn": "ATSAMD21G18"})
        b = _frag(source_id="bom://row-3", metadata={"mpn": "ATSAMD21G18"})
        clusters = IdentityResolver().resolve([a, b])
        assert len(clusters) == 1
        assert sorted(clusters[0].fragment_indices) == [0, 1]
        assert a.metadata["resolved_identity"] == b.metadata["resolved_identity"]

    def test_different_mpn_stays_separate(self) -> None:
        a = _frag(source_id="schematic://U1", metadata={"mpn": "ATSAMD21G18"})
        b = _frag(source_id="bom://row-7", metadata={"mpn": "STM32F103C8"})
        clusters = IdentityResolver().resolve([a, b])
        assert len(clusters) == 2

    def test_ref_des_alone_links_fragments(self) -> None:
        a = _frag(source_id="schematic://r12", metadata={"ref_des": "R12"})
        b = _frag(source_id="bom://r12", metadata={"ref_des": "R12"})
        clusters = IdentityResolver().resolve([a, b])
        assert len(clusters) == 1

    def test_work_product_id_matches(self) -> None:
        wp = uuid4()
        a = _frag(source_id="cad://A", metadata={"work_product_id": str(wp)})
        b = _frag(source_id="thread://B", metadata={"work_product_id": str(wp)})
        clusters = IdentityResolver().resolve([a, b])
        assert len(clusters) == 1
        assert clusters[0].canonical == str(wp)

    def test_fragment_with_no_tokens_is_skipped(self) -> None:
        a = _frag(source_id="random://", content="some unrelated prose")
        clusters = IdentityResolver().resolve([a])
        assert clusters == []
        assert "resolved_identity" not in a.metadata

    def test_extracts_ref_des_from_content_when_metadata_absent(self) -> None:
        a = _frag(
            source_id="schematic://body-only",
            content="Capacitor C42 placed near the regulator output.",
        )
        b = _frag(source_id="bom://row-c42", metadata={"ref_des": "C42"})
        clusters = IdentityResolver().resolve([a, b])
        assert len(clusters) == 1


# ---------------------------------------------------------------------------
# orphans / mismatches
# ---------------------------------------------------------------------------


class TestOrphans:
    def test_orphan_returned_when_only_one_fragment_in_cluster(self) -> None:
        a = _frag(source_id="bom://row-x", metadata={"mpn": "OBSCURE-123"})
        b = _frag(source_id="schematic://U1", metadata={"mpn": "ATSAMD21G18"})
        c = _frag(source_id="bom://row-u1", metadata={"mpn": "ATSAMD21G18"})
        orphans = IdentityResolver().orphans([a, b, c])
        assert [o.source_id for o in orphans] == ["bom://row-x"]


class TestMismatches:
    def test_same_ref_des_different_mpn_is_a_mismatch(self) -> None:
        a = _frag(
            source_id="schematic://R12",
            metadata={"ref_des": "R12", "mpn": "ERJ-3EKF1002V"},
        )
        b = _frag(
            source_id="bom://R12",
            metadata={"ref_des": "R12", "mpn": "RC0603FR-071K"},
        )
        mismatches = IdentityResolver().mismatches([a, b])
        assert len(mismatches) == 1
        m = mismatches[0]
        assert m.field == "mpn"
        assert m.weak_field == "ref_des"
        assert {m.value_a, m.value_b} == {"ERJ-3EKF1002V", "RC0603FR-071K"}

    def test_no_mismatch_when_strong_tokens_agree(self) -> None:
        a = _frag(
            source_id="schematic://R12",
            metadata={"ref_des": "R12", "mpn": "ERJ-3EKF1002V"},
        )
        b = _frag(
            source_id="bom://R12",
            metadata={"ref_des": "R12", "mpn": "ERJ-3EKF1002V"},
        )
        assert IdentityResolver().mismatches([a, b]) == []

    def test_no_mismatch_when_only_one_side_has_strong_token(self) -> None:
        a = _frag(source_id="schematic://U1", metadata={"ref_des": "U1", "mpn": "X-1"})
        b = _frag(source_id="bom://U1", metadata={"ref_des": "U1"})  # no mpn
        assert IdentityResolver().mismatches([a, b]) == []

    def test_mismatches_dedupe_pair_per_strong_field(self) -> None:
        a = _frag(
            source_id="A",
            metadata={"ref_des": "U1", "mpn": "X-1", "work_product_id": str(uuid4())},
        )
        b = _frag(
            source_id="B",
            metadata={"ref_des": "U1", "mpn": "X-2", "work_product_id": str(uuid4())},
        )
        mismatches = IdentityResolver().mismatches([a, b])
        # Two strong fields disagree → two mismatch rows, one per field.
        assert {m.field for m in mismatches} == {"mpn", "work_product_id"}
        assert len(mismatches) == 2


# ---------------------------------------------------------------------------
# union-find correctness via transitive linking
# ---------------------------------------------------------------------------


class TestTransitiveLinking:
    def test_three_fragments_chain_via_shared_tokens(self) -> None:
        # A↔B share MPN, B↔C share ref_des → all three are one cluster.
        a = _frag(source_id="A", metadata={"mpn": "M-1"})
        b = _frag(source_id="B", metadata={"mpn": "M-1", "ref_des": "U1"})
        c = _frag(source_id="C", metadata={"ref_des": "U1"})
        clusters = IdentityResolver().resolve([a, b, c])
        assert len(clusters) == 1
        assert sorted(clusters[0].fragment_indices) == [0, 1, 2]
