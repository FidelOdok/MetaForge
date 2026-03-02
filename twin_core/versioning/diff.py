"""Diff computation — compare two version snapshots to produce change entries."""

from uuid import UUID

from twin_core.models.version import ArtifactChange, VersionDiff


def compute_diff(
    snapshot_a: dict[UUID, str],
    snapshot_b: dict[UUID, str],
    version_a_id: UUID,
    version_b_id: UUID,
) -> VersionDiff:
    """Compare two snapshots and return a VersionDiff with all changes.

    Args:
        snapshot_a: artifact_id → content_hash at version A.
        snapshot_b: artifact_id → content_hash at version B.
        version_a_id: ID of version A.
        version_b_id: ID of version B.

    Returns:
        VersionDiff listing added, modified, and deleted artifacts.
    """
    changes: list[ArtifactChange] = []
    all_ids = set(snapshot_a) | set(snapshot_b)

    for artifact_id in sorted(all_ids):
        in_a = artifact_id in snapshot_a
        in_b = artifact_id in snapshot_b

        if in_b and not in_a:
            changes.append(
                ArtifactChange(
                    artifact_id=artifact_id,
                    change_type="added",
                    new_content_hash=snapshot_b[artifact_id],
                )
            )
        elif in_a and not in_b:
            changes.append(
                ArtifactChange(
                    artifact_id=artifact_id,
                    change_type="deleted",
                    old_content_hash=snapshot_a[artifact_id],
                )
            )
        elif snapshot_a[artifact_id] != snapshot_b[artifact_id]:
            changes.append(
                ArtifactChange(
                    artifact_id=artifact_id,
                    change_type="modified",
                    old_content_hash=snapshot_a[artifact_id],
                    new_content_hash=snapshot_b[artifact_id],
                )
            )

    return VersionDiff(
        version_a=version_a_id,
        version_b=version_b_id,
        changes=changes,
    )
