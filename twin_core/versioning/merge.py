"""Three-way merge — conflict detection and merge execution for version snapshots."""

from uuid import UUID

from pydantic import BaseModel


class ConflictDetail(BaseModel):
    """Description of a single merge conflict."""

    artifact_id: UUID
    conflict_type: str  # "content" or "structural"
    source_hash: str | None = None
    target_hash: str | None = None


class MergeConflict(Exception):
    """Raised when a three-way merge encounters unresolvable conflicts."""

    def __init__(self, conflicts: list[ConflictDetail]) -> None:
        self.conflicts = conflicts
        ids = ", ".join(str(c.artifact_id)[:8] for c in conflicts)
        super().__init__(f"Merge conflicts on artifacts: {ids}")


def detect_conflicts(
    ancestor: dict[UUID, str],
    source: dict[UUID, str],
    target: dict[UUID, str],
) -> list[ConflictDetail]:
    """Detect conflicts between source and target relative to their common ancestor.

    Conflict rules:
    - **Content conflict**: both sides modified the same artifact with different hashes.
    - **Structural conflict**: one side deleted an artifact the other side modified.

    Returns:
        List of ConflictDetail (empty if merge is clean).
    """
    conflicts: list[ConflictDetail] = []
    all_ids = set(ancestor) | set(source) | set(target)

    for aid in sorted(all_ids):
        anc_hash = ancestor.get(aid)
        src_hash = source.get(aid)
        tgt_hash = target.get(aid)

        src_changed = src_hash != anc_hash
        tgt_changed = tgt_hash != anc_hash

        if not src_changed or not tgt_changed:
            continue  # At most one side changed — no conflict

        # Both sides changed relative to ancestor
        if src_hash == tgt_hash:
            continue  # Both made the same change — no conflict

        # Determine conflict type
        if src_hash is None or tgt_hash is None:
            # One side deleted, other modified (or added differently)
            conflicts.append(
                ConflictDetail(
                    artifact_id=aid,
                    conflict_type="structural",
                    source_hash=src_hash,
                    target_hash=tgt_hash,
                )
            )
        else:
            # Both modified with different hashes
            conflicts.append(
                ConflictDetail(
                    artifact_id=aid,
                    conflict_type="content",
                    source_hash=src_hash,
                    target_hash=tgt_hash,
                )
            )

    return conflicts


def perform_merge(
    ancestor: dict[UUID, str],
    source: dict[UUID, str],
    target: dict[UUID, str],
) -> dict[UUID, str]:
    """Execute a three-way merge, raising MergeConflict if any conflicts exist.

    Non-conflicting changes from both sides are combined into the merged snapshot.

    Args:
        ancestor: Snapshot at the common ancestor.
        source: Snapshot at the source branch HEAD.
        target: Snapshot at the target branch HEAD.

    Returns:
        Merged snapshot (artifact_id → content_hash).

    Raises:
        MergeConflict: If any artifacts have conflicting changes.
    """
    conflicts = detect_conflicts(ancestor, source, target)
    if conflicts:
        raise MergeConflict(conflicts)

    # Start from target (the branch being merged into), then apply source changes
    merged: dict[UUID, str] = dict(target)
    all_ids = set(ancestor) | set(source) | set(target)

    for aid in all_ids:
        anc_hash = ancestor.get(aid)
        src_hash = source.get(aid)
        tgt_hash = target.get(aid)

        src_changed = src_hash != anc_hash
        tgt_changed = tgt_hash != anc_hash

        if src_changed and not tgt_changed:
            # Source made a change, target did not — take source's value
            if src_hash is None:
                merged.pop(aid, None)
            else:
                merged[aid] = src_hash
        # If target changed (or neither changed), merged already has the right value

    return merged
