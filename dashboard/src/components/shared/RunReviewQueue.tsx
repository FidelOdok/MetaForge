import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { useRuns } from '../../hooks/use-runs';
/** Run approvals and work-product proposals use separate gateway contracts. */
export function RunReviewQueue() {
  const query = useRuns();
  const pending = (query.data ?? []).filter(run=>run.status==='awaiting_approval');
  return <section className="context-panel run-review-queue" aria-labelledby="run-gates-heading"><div className="section-heading"><h2 id="run-gates-heading">Run approval gates</h2><span className="eyebrow">ALL PROJECTS</span></div>{query.isLoading?<p className="review-queue-message" role="status">Loading run gates…</p>:query.isError?<p className="review-queue-message" role="alert">Run gates could not be loaded. Check your gateway connection.</p>:pending.length?<div className="review-list">{pending.map(run=><Link key={run.id} to={`/runs/${run.id}`}><strong>{String(run.request.goal??run.id)}</strong><span>{run.approvalReason??'Inspect the evidence before approving execution.'}</span><span className="text-action">Inspect and decide<ArrowUpRight size={16}/></span></Link>)}</div>:<p className="review-queue-message">No runs awaiting approval.</p>}</section>;
}
