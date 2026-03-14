"""Diff computation — compare two version snapshots to produce change entries."""

from uuid import UUID

from twin_core.models.version import VersionDiff, WorkProductChange


def compute_diff(
    snapshot_a: dict[UUID, str],
    snapshot_b: dict[UUID, str],
    version_a_id: UUID,
    version_b_id: UUID,
) -> VersionDiff:
    """Compare two snapshots and return a VersionDiff with all changes.

    Args:
        snapshot_a: work_product_id → content_hash at version A.
        snapshot_b: work_product_id → content_hash at version B.
        version_a_id: ID of version A.
        version_b_id: ID of version B.

    Returns:
        VersionDiff listing added, modified, and deleted work_products.
    """
    changes: list[WorkProductChange] = []
    all_ids = set(snapshot_a) | set(snapshot_b)

    for work_product_id in sorted(all_ids):
        in_a = work_product_id in snapshot_a
        in_b = work_product_id in snapshot_b

        if in_b and not in_a:
            changes.append(
                WorkProductChange(
                    work_product_id=work_product_id,
                    change_type="added",
                    new_content_hash=snapshot_b[work_product_id],
                )
            )
        elif in_a and not in_b:
            changes.append(
                WorkProductChange(
                    work_product_id=work_product_id,
                    change_type="deleted",
                    old_content_hash=snapshot_a[work_product_id],
                )
            )
        elif snapshot_a[work_product_id] != snapshot_b[work_product_id]:
            changes.append(
                WorkProductChange(
                    work_product_id=work_product_id,
                    change_type="modified",
                    old_content_hash=snapshot_a[work_product_id],
                    new_content_hash=snapshot_b[work_product_id],
                )
            )

    return VersionDiff(
        version_a=version_a_id,
        version_b=version_b_id,
        changes=changes,
    )
