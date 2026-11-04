import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Box, CheckCircle2, Info, Ruler, ShieldAlert, Star } from 'lucide-react';
import { assistantKeys, useDecideProposal, useProposals } from '../../hooks/use-assistant';
import type {
  Basis,
  CalloutBlock,
  ChoiceBlock,
  ComparisonBlock,
  MeasurementRequestBlock,
  MetricsBlock,
  PartBlock,
  ProposalBlock,
  TableBlock,
  UiBlock,
} from '../../types/ui-blocks';

// Components for the typed blocks on an agent reply (generative UI). Every
// interaction is an ordinary, visible act by the person: a choice or a
// measurement is sent as their reply, a proposal is decided through the same
// approval API as the Changes tab, and nothing runs on the agent's say-so.

export interface BlockActions {
  /** Send text as the person's reply in this conversation. */
  onReply?: (text: string) => void;
  /** Show a twin work product (select it on stage or in the graph). */
  onShowPart?: (nodeId: string) => void;
  /** After a proposal is approved or rejected here. */
  onDecided?: (decision: 'approve' | 'reject') => void;
  projectId?: string | null;
  /** Interactions are off for a reply that is no longer the latest, or while one is sending. */
  interactive?: boolean;
}

const BASIS_LABEL: Record<Basis, string> = {
  illustrative: 'Illustrative',
  estimate: 'Estimate',
  calculated: 'Calculated',
  measured: 'Measured',
  verified: 'Verified',
};

function BasisTag({ basis }: { basis?: Basis }) {
  if (!basis) return null;
  return (
    <span className={`mb-basis mb-basis-${basis}`} title="How these figures were arrived at">
      {BASIS_LABEL[basis]}
    </span>
  );
}

function Choice({ block, onReply, interactive }: { block: ChoiceBlock } & BlockActions) {
  const [picked, setPicked] = useState<string[]>([]);
  const [sent, setSent] = useState<string | null>(null);
  const disabled = !interactive || !onReply || sent !== null;
  const valueOf = (o: ChoiceBlock['options'][number]) => o.value ?? o.label;
  const answer = (text: string) => {
    if (disabled) return;
    setSent(text);
    onReply?.(text);
  };
  return (
    <fieldset className="mb-block mb-choice" disabled={disabled}>
      <legend>{block.prompt}</legend>
      <div className="mb-choice-options">
        {block.options.map((o) =>
          block.multiple ? (
            <label key={o.label} className={`mb-option ${picked.includes(valueOf(o)) ? 'is-on' : ''}`}>
              <input
                type="checkbox"
                checked={picked.includes(valueOf(o))}
                onChange={(e) =>
                  setPicked((p) => (e.target.checked ? [...p, valueOf(o)] : p.filter((v) => v !== valueOf(o))))
                }
              />
              <span>
                <strong>{o.label}</strong>
                {o.description && <small>{o.description}</small>}
              </span>
            </label>
          ) : (
            <button
              key={o.label}
              type="button"
              className={`mb-option ${sent === valueOf(o) ? 'is-on' : ''}`}
              onClick={() => answer(valueOf(o))}
            >
              <strong>{o.label}</strong>
              {o.description && <small>{o.description}</small>}
            </button>
          ),
        )}
      </div>
      {block.multiple && (
        <button
          type="button"
          className="mb-action"
          disabled={disabled || picked.length === 0}
          onClick={() => answer(picked.join(', '))}
        >
          Send
        </button>
      )}
      {sent !== null && <p className="mb-note">You answered: {sent}</p>}
    </fieldset>
  );
}

function Comparison({ block }: { block: ComparisonBlock }) {
  const criteria = block.criteria ?? [];
  return (
    <section className="mb-block mb-comparison" aria-label={block.title ?? 'Options compared'}>
      {(block.title || block.basis) && (
        <header>
          {block.title && <h4>{block.title}</h4>}
          <BasisTag basis={block.basis} />
        </header>
      )}
      <div className="mb-comparison-grid">
        {block.options.map((o) => (
          <article key={o.name} className={`mb-compare-option ${o.recommended ? 'is-recommended' : ''}`}>
            <h5>
              {o.name}
              {o.recommended && (
                <span className="mb-recommended">
                  <Star size={11} aria-hidden="true" /> Recommended
                </span>
              )}
            </h5>
            {o.summary && <p>{o.summary}</p>}
            {criteria.length > 0 && (
              <dl>
                {criteria.map((c) => (
                  <div key={c}>
                    <dt>{c}</dt>
                    <dd>{o.values?.[c] ?? '–'}</dd>
                  </div>
                ))}
              </dl>
            )}
            {!!o.pros?.length && (
              <ul className="mb-pros" aria-label="For">
                {o.pros.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
            {!!o.cons?.length && (
              <ul className="mb-cons" aria-label="Against">
                {o.cons.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function MeasurementRequest({ block, onReply, interactive }: { block: MeasurementRequestBlock } & BlockActions) {
  const [value, setValue] = useState(block.current !== undefined ? String(block.current) : '');
  const [sent, setSent] = useState(false);
  const number = Number(value);
  const valid = value.trim() !== '' && Number.isFinite(number) && number > 0;
  const disabled = !interactive || !onReply || sent;
  return (
    <form
      className="mb-block mb-measure"
      onSubmit={(e) => {
        e.preventDefault();
        if (disabled || !valid) return;
        setSent(true);
        onReply?.(`${block.label}: ${number} ${block.unit} (measured by me)`);
      }}
    >
      <label>
        <span className="mb-measure-label">
          <Ruler size={14} aria-hidden="true" /> {block.label}
          {block.critical && <span className="mb-critical">Critical</span>}
        </span>
        {block.why && <small>{block.why}</small>}
        <span className="mb-measure-row">
          <input
            inputMode="decimal"
            value={value}
            disabled={disabled}
            aria-label={`${block.label} in ${block.unit}`}
            onChange={(e) => setValue(e.target.value)}
          />
          <span className="mb-unit">{block.unit}</span>
          <button type="submit" className="mb-action" disabled={disabled || !valid}>
            {sent ? 'Sent' : 'Send'}
          </button>
        </span>
      </label>
      {block.current !== undefined && !sent && <p className="mb-note">Current value is an estimate; confirm or correct it.</p>}
    </form>
  );
}

function Table({ block }: { block: TableBlock }) {
  return (
    <section className="mb-block mb-table" aria-label={block.title ?? 'Table'}>
      {(block.title || block.basis) && (
        <header>
          {block.title && <h4>{block.title}</h4>}
          <BasisTag basis={block.basis} />
        </header>
      )}
      <div className="mb-table-scroll">
        <table>
          <thead>
            <tr>
              {block.columns.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className={typeof cell === 'number' ? 'is-num' : undefined}>
                    {cell ?? '–'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Metrics({ block }: { block: MetricsBlock }) {
  return (
    <section className="mb-block mb-metrics" aria-label={block.title ?? 'Figures'}>
      {(block.title || block.basis) && (
        <header>
          {block.title && <h4>{block.title}</h4>}
          <BasisTag basis={block.basis} />
        </header>
      )}
      <ul>
        {block.items.map((m) => {
          const hasLimit = m.limit !== undefined && m.limit !== 0;
          const over = hasLimit && (m.limit_kind === 'min' ? m.value < m.limit! : m.value > m.limit!);
          const ratio = hasLimit ? Math.min(Math.abs(m.value / m.limit!), 1.25) : null;
          return (
            <li key={m.label} className={over ? 'is-over' : hasLimit ? 'is-within' : undefined}>
              <span className="mb-metric-label">{m.label}</span>
              <span className="mb-metric-value">
                {m.value}
                {m.unit && ` ${m.unit}`}
                {hasLimit && (
                  <small>
                    {' '}
                    {m.limit_kind === 'min' ? 'min' : 'limit'} {m.limit}
                    {m.unit && ` ${m.unit}`}
                  </small>
                )}
              </span>
              {ratio !== null && (
                <span className="mb-meter" aria-hidden="true">
                  <span style={{ width: `${(ratio / 1.25) * 100}%` }} />
                  <i style={{ left: `${(1 / 1.25) * 100}%` }} />
                </span>
              )}
              {over && <span className="mb-sr">{m.limit_kind === 'min' ? 'Below its minimum' : 'Over its limit'}</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const CALLOUT_ICON = { info: Info, warning: AlertTriangle, risk: ShieldAlert, success: CheckCircle2 } as const;

function Callout({ block }: { block: CalloutBlock }) {
  const tone = block.tone ?? 'info';
  const Icon = CALLOUT_ICON[tone];
  return (
    <aside className={`mb-block mb-callout mb-tone-${tone}`} role={tone === 'risk' || tone === 'warning' ? 'note' : undefined}>
      <Icon size={16} aria-hidden="true" />
      <div>
        <strong>{block.title}</strong>
        {block.body && <p>{block.body}</p>}
      </div>
    </aside>
  );
}

function Proposal({ block, projectId, interactive, onDecided }: { block: ProposalBlock } & BlockActions) {
  const queryClient = useQueryClient();
  const proposals = useProposals(projectId ?? undefined);
  const decide = useDecideProposal();
  const [reason, setReason] = useState('');
  const record = proposals.data?.proposals.find((p) => p.change_id === block.change_id);
  const status = decide.data?.status ?? record?.status;
  const pending = status === 'pending';
  const run = (decision: 'approve' | 'reject') =>
    decide.mutate(
      {
        changeId: block.change_id,
        decision,
        reason: reason.trim() || (decision === 'approve' ? 'Approved in the conversation' : 'Rejected in the conversation'),
        reviewer: 'dashboard-user',
      },
      {
        onSuccess: () => {
          void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
          onDecided?.(decision);
        },
      },
    );
  return (
    <section className="mb-block mb-proposal" aria-label="Proposed change">
      <header>
        <h4>{block.title ?? 'Proposed change'}</h4>
        <span className={`mb-status mb-status-${status ?? 'unknown'}`}>
          {status ? status.replace(/_/g, ' ') : proposals.isLoading ? 'Loading' : 'Not found'}
        </span>
      </header>
      <p>{block.summary ?? record?.description ?? 'A change to the design, waiting for your decision.'}</p>
      {!!record?.work_products_affected.length && (
        <p className="mb-note">Affects {record.work_products_affected.length} work product(s). Nothing changes until you approve.</p>
      )}
      {pending && interactive !== false && (
        <div className="mb-proposal-actions">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional)"
            aria-label="Reason for your decision"
          />
          <button type="button" className="mb-action is-primary" disabled={decide.isPending} onClick={() => run('approve')}>
            Approve
          </button>
          <button type="button" className="mb-action" disabled={decide.isPending} onClick={() => run('reject')}>
            Reject
          </button>
        </div>
      )}
      {decide.isError && <p className="mb-note mb-error">The decision was not recorded. Try again.</p>}
    </section>
  );
}

function Part({ block, onShowPart }: { block: PartBlock } & BlockActions) {
  return (
    <section className="mb-block mb-part">
      <Box size={16} aria-hidden="true" />
      <div>
        <strong>{block.label ?? block.node_id}</strong>
        {block.note && <p>{block.note}</p>}
      </div>
      {onShowPart && (
        <button type="button" className="mb-action" onClick={() => onShowPart(block.node_id)}>
          Show
        </button>
      )}
    </section>
  );
}

export function UiBlockView({ block, ...actions }: { block: UiBlock } & BlockActions) {
  switch (block.type) {
    case 'choice':
      return <Choice block={block} {...actions} />;
    case 'comparison':
      return <Comparison block={block} />;
    case 'measurement_request':
      return <MeasurementRequest block={block} {...actions} />;
    case 'table':
      return <Table block={block} />;
    case 'metrics':
      return <Metrics block={block} />;
    case 'callout':
      return <Callout block={block} />;
    case 'proposal':
      return <Proposal block={block} {...actions} />;
    case 'part':
      return <Part block={block} {...actions} />;
    default:
      // A block type this dashboard does not know yet: say so rather than drop it.
      return <p className="mb-block mb-note">This reply includes content this version cannot show.</p>;
  }
}
