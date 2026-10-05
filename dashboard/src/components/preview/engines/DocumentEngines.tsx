import { Fragment, type ReactNode } from 'react';
import { parseInline, parseMarkdown, type MdBlock, type MdInline } from '../parsers/markdown';
import { parseConstraintSet, parseDecision } from '../parsers/records';
import { PC } from '../tokens';
import type { TwinNode } from '../../../types/twin';

// ── Markdown ────────────────────────────────────────────────────────────────

function Inline({ nodes }: { nodes: MdInline[] }) {
  return (
    <>
      {nodes.map((n, i) => {
        switch (n.kind) {
          case 'text':
            return <Fragment key={i}>{n.text}</Fragment>;
          case 'code':
            return (
              <code key={i} className="font-mono" style={{ fontSize: '0.9em', background: PC.surfaceHigh, padding: '1px 4px', borderRadius: 3 }}>
                {n.text}
              </code>
            );
          case 'strong':
            return <strong key={i} style={{ color: PC.onSurface }}><Inline nodes={n.children} /></strong>;
          case 'em':
            return <em key={i}><Inline nodes={n.children} /></em>;
          case 'link':
            return (
              <a key={i} href={n.href} target="_blank" rel="noreferrer noopener" style={{ color: PC.teal }}>
                <Inline nodes={n.children} />
              </a>
            );
          default:
            return null;
        }
      })}
    </>
  );
}

export function InlineText({ text }: { text: string }) {
  return <Inline nodes={parseInline(text)} />;
}

const HEADING_SIZES = [0, 20, 16, 14, 13, 12, 12];

function Block({ block }: { block: MdBlock }): ReactNode {
  switch (block.kind) {
    case 'heading': {
      const Tag = `h${block.level}` as 'h1';
      return (
        <Tag style={{ fontSize: HEADING_SIZES[block.level], fontWeight: 600, color: PC.onSurface, margin: '1.1em 0 0.5em', lineHeight: 1.3 }}>
          <InlineText text={block.text} />
        </Tag>
      );
    }
    case 'paragraph':
      return <p style={{ margin: '0 0 0.8em' }}><InlineText text={block.text} /></p>;
    case 'code':
      return (
        <pre className="font-mono" style={{ margin: '0 0 0.9em', padding: 12, fontSize: 11, lineHeight: 1.55, background: PC.surfaceLow, border: `1px solid ${PC.border}`, borderRadius: 6, overflowX: 'auto' }}>
          {block.text}
        </pre>
      );
    case 'quote':
      return (
        <blockquote style={{ margin: '0 0 0.9em', padding: '2px 12px', borderLeft: `3px solid ${PC.orange}`, color: PC.onSurfaceVariant }}>
          {block.blocks.map((b, i) => <Block key={i} block={b} />)}
        </blockquote>
      );
    case 'rule':
      return <hr style={{ border: 'none', borderTop: `1px solid ${PC.border}`, margin: '1em 0' }} />;
    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul';
      return (
        <Tag style={{ margin: '0 0 0.9em', paddingLeft: 20, listStyle: block.ordered ? 'decimal' : 'disc' }}>
          {block.items.map((item, i) => (
            <li key={i} style={{ marginLeft: item.depth * 16, marginBottom: 2, listStyle: item.checked !== undefined ? 'none' : undefined }}>
              {item.checked !== undefined && (
                <span className="material-symbols-outlined" aria-label={item.checked ? 'done' : 'open'} style={{ fontSize: 13, verticalAlign: 'middle', marginRight: 4, marginLeft: -18, color: item.checked ? PC.green : PC.onSurfaceVariant }}>
                  {item.checked ? 'check_box' : 'check_box_outline_blank'}
                </span>
              )}
              <InlineText text={item.text} />
            </li>
          ))}
        </Tag>
      );
    }
    case 'table':
      return (
        <div style={{ overflowX: 'auto', margin: '0 0 0.9em' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '50%' }}>
            <thead>
              <tr>
                {block.header.map((h, i) => (
                  <th key={i} style={{ textAlign: 'left', padding: '5px 10px', borderBottom: `1px solid ${PC.borderMid}`, color: PC.onSurfaceVariant, fontWeight: 600 }}>
                    <InlineText text={h} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((c, i) => (
                    <td key={i} style={{ padding: '5px 10px', borderBottom: `1px solid ${PC.border}`, verticalAlign: 'top' }}>
                      <InlineText text={c} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return null;
  }
}

export function MarkdownView({ source }: { source: string }) {
  const blocks = parseMarkdown(source);
  return (
    <div data-testid="preview-markdown" style={{ fontSize: 13, lineHeight: 1.6, color: PC.onSurface }}>
      {blocks.length === 0 ? (
        <p style={{ color: PC.onSurfaceVariant }}>This document is empty.</p>
      ) : (
        blocks.map((b, i) => <Block key={i} block={b} />)
      )}
    </div>
  );
}

// ── Requirements (constraint_set) ───────────────────────────────────────────

const SEVERITY_TONE: Record<string, { fg: string; bg: string }> = {
  error: { fg: PC.red, bg: PC.redFaint },
  warning: { fg: PC.amber, bg: PC.amberFaint },
  info: { fg: PC.teal, bg: 'rgba(134,207,255,0.12)' },
};

function Pill({ label, tone }: { label: string; tone?: { fg: string; bg: string } }) {
  const t = tone ?? { fg: PC.onSurfaceVariant, bg: PC.surfaceHigh };
  return (
    <span className="font-mono uppercase" style={{ fontSize: 9, letterSpacing: '0.06em', color: t.fg, background: t.bg, padding: '2px 6px', borderRadius: 3, whiteSpace: 'nowrap' }}>
      {label}
    </span>
  );
}

export function RequirementsPreview({ source }: { source: string }) {
  const { rows } = parseConstraintSet(source);
  // Not in the recorder's shape: show it as the document it is.
  if (rows.length === 0) return <MarkdownView source={source} />;

  return (
    <div data-testid="preview-requirements" style={{ overflowX: 'auto' }}>
      <div className="font-mono uppercase mb-2" style={{ fontSize: 10, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>
        {rows.length} requirement{rows.length === 1 ? '' : 's'}
      </div>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
        <thead>
          <tr>
            {['Requirement', 'Severity', 'Domain', 'Acceptance criteria', 'Verification', 'Binding'].map((h) => (
              <th key={h} style={{ textAlign: 'left', padding: '6px 8px', borderBottom: `1px solid ${PC.borderMid}`, color: PC.onSurfaceVariant, fontWeight: 600, whiteSpace: 'nowrap' }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.name}-${i}`} style={{ verticalAlign: 'top' }}>
              <td style={{ padding: '6px 8px', borderBottom: `1px solid ${PC.border}`, color: PC.onSurface }}>
                <div className="font-mono" style={{ fontSize: 11 }}>{r.name}</div>
                {r.message && <div style={{ color: PC.onSurfaceVariant, marginTop: 2 }}>{r.message}</div>}
              </td>
              <td style={{ padding: '6px 8px', borderBottom: `1px solid ${PC.border}` }}>
                <Pill label={r.severity} tone={SEVERITY_TONE[r.severity.toLowerCase()]} />
              </td>
              <td className="font-mono" style={{ padding: '6px 8px', borderBottom: `1px solid ${PC.border}`, fontSize: 11 }}>{r.domain}</td>
              <td style={{ padding: '6px 8px', borderBottom: `1px solid ${PC.border}` }}>{r.acceptanceCriteria || '-'}</td>
              <td style={{ padding: '6px 8px', borderBottom: `1px solid ${PC.border}` }}>{r.verificationMethod || '-'}</td>
              <td className="font-mono" style={{ padding: '6px 8px', borderBottom: `1px solid ${PC.border}`, fontSize: 11 }}>
                {r.binding || (r.expression ? <code title="Constraint expression">{r.expression}</code> : '-')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Decision card (design_decision) ─────────────────────────────────────────

const STATUS_TONE: Record<string, { fg: string; bg: string }> = {
  valid: { fg: PC.green, bg: PC.greenFaint },
  approved: { fg: PC.green, bg: PC.greenFaint },
  accepted: { fg: PC.green, bg: PC.greenFaint },
  warning: { fg: PC.amber, bg: PC.amberFaint },
  proposed: { fg: PC.amber, bg: PC.amberFaint },
  error: { fg: PC.red, bg: PC.redFaint },
  superseded: { fg: PC.onSurfaceVariant, bg: PC.surfaceHigh },
};

export function DecisionCard({ node, source }: { node: TwinNode; source: string | null }) {
  const parsed = parseDecision(source ?? '');
  const props = node.properties;
  const title = parsed.title || node.name;
  const rationale = parsed.rationale || (typeof props.rationale === 'string' ? props.rationale : '');
  const status = String(props.decision_status ?? props.status ?? node.status ?? 'unknown');
  const supersedes = parsed.supersedes || (typeof props.supersedes === 'string' ? props.supersedes : '');

  return (
    <article data-testid="preview-decision" className="rounded" style={{ border: `1px solid ${PC.border}`, background: PC.surfaceLow, padding: 16 }}>
      <header className="flex items-start gap-2 mb-3" style={{ flexWrap: 'wrap' }}>
        <span className="material-symbols-outlined" style={{ fontSize: 18, color: PC.orange }}>gavel</span>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: PC.onSurface, margin: 0, flex: 1, minWidth: 0 }}>{title}</h2>
        <span aria-label="Decision status">
          <Pill label={status} tone={STATUS_TONE[status.toLowerCase()]} />
        </span>
      </header>
      {supersedes && (
        <div className="font-mono mb-3" style={{ fontSize: 11, color: PC.onSurfaceVariant }}>
          Supersedes {supersedes}
        </div>
      )}
      <section className="mb-3">
        <div className="font-mono uppercase mb-1" style={{ fontSize: 10, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>Rationale</div>
        {rationale ? <MarkdownView source={rationale} /> : <p style={{ fontSize: 12, color: PC.onSurfaceVariant, margin: 0 }}>No rationale was recorded.</p>}
      </section>
      <section>
        <div className="font-mono uppercase mb-1" style={{ fontSize: 10, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>
          Alternatives considered ({parsed.alternatives.length})
        </div>
        {parsed.alternatives.length === 0 ? (
          <p style={{ fontSize: 12, color: PC.onSurfaceVariant, margin: 0 }}>No alternatives were recorded.</p>
        ) : (
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {parsed.alternatives.map((a, i) => (
              <li key={i} className="rounded" style={{ background: PC.surfaceHigh, padding: '6px 10px', marginBottom: 6, fontSize: 12 }}>
                <div style={{ color: PC.onSurface, fontWeight: 500 }}>{a.option || 'Unnamed option'}</div>
                {a.reasonRejected && <div style={{ color: PC.onSurfaceVariant, marginTop: 2 }}>Rejected: {a.reasonRejected}</div>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
