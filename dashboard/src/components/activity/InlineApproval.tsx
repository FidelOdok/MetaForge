import { useApproval } from '../../hooks/use-approvals';
import { ApprovalCard } from '../approvals/ApprovalCard';

/** The existing approval card, unchanged: no second approval UI. */
export function InlineApproval({ id }: { id: string }) {
  const approval = useApproval(id);
  if (approval.data) return <ApprovalCard item={approval.data} />;
  return <p className="act-muted">{approval.isError ? 'This review could not be loaded.' : 'Loading the review…'}</p>;
}
