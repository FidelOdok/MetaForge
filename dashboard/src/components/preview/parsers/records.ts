/**
 * Structured readers for the Markdown documents MetaForge's own recorders
 * write, so the preview can show them as what they are (a requirements
 * table, a decision card) instead of as prose.
 *
 * Formats mirror api_gateway/twin/constraint_recorder.py (`_render_markdown`)
 * and api_gateway/twin/decision_recorder.py (`render_decision_markdown`).
 * When a document does not match, the readers return nothing and the engine
 * falls back to rendering the Markdown.
 */
import { splitTableRow } from './markdown';

export interface RequirementRow {
  name: string;
  severity: string;
  domain: string;
  message: string;
  acceptanceCriteria: string;
  verificationMethod: string;
  expectedEvidence: string;
  binding: string;
  expression: string;
}

const REQ_HEADING = /^##\s+(.+?)\s+\(([^,()]+),\s*([^()]+)\)\s*$/;
const LABELLED = /^\*\*(Acceptance criteria|Verification method|Expected evidence|Binding):\*\*\s*(.*)$/;

/** Parse a constraint-set document into rows; [] when it is not one. */
export function parseConstraintSet(md: string): { title: string; rows: RequirementRow[] } {
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  let title = '';
  const rows: RequirementRow[] = [];
  let cur: RequirementRow | null = null;
  let inCode = false;
  const code: string[] = [];

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inCode && cur) cur.expression = code.join('\n').trim();
      code.length = 0;
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }
    const t = /^#\s+(?:Constraint set:\s*)?(.*)$/.exec(line);
    if (t && !line.startsWith('##')) {
      title = (t[1] ?? '').trim();
      continue;
    }
    const h = REQ_HEADING.exec(line);
    if (h) {
      cur = {
        name: (h[1] ?? '').trim(),
        severity: (h[2] ?? '').trim(),
        domain: (h[3] ?? '').trim(),
        message: '',
        acceptanceCriteria: '',
        verificationMethod: '',
        expectedEvidence: '',
        binding: '',
        expression: '',
      };
      rows.push(cur);
      continue;
    }
    if (!cur || line.trim() === '') continue;
    const l = LABELLED.exec(line.trim());
    if (l) {
      const value = (l[2] ?? '').trim();
      if (l[1] === 'Acceptance criteria') cur.acceptanceCriteria = value;
      else if (l[1] === 'Verification method') cur.verificationMethod = value;
      else if (l[1] === 'Expected evidence') cur.expectedEvidence = value;
      else cur.binding = value;
    } else {
      cur.message = cur.message ? `${cur.message} ${line.trim()}` : line.trim();
    }
  }
  return { title, rows };
}

export interface DecisionAlternativeRow {
  option: string;
  reasonRejected: string;
}

export interface DecisionRecord {
  title: string;
  rationale: string;
  alternatives: DecisionAlternativeRow[];
  supersedes: string;
}

/** Parse a decision record. Any section may be missing. */
export function parseDecision(md: string): DecisionRecord {
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  const out: DecisionRecord = { title: '', rationale: '', alternatives: [], supersedes: '' };
  let section = '';
  const rationale: string[] = [];

  for (const line of lines) {
    const h1 = /^#\s+(.*)$/.exec(line);
    if (h1) {
      out.title = (h1[1] ?? '').trim();
      continue;
    }
    const h2 = /^##\s+(.*)$/.exec(line);
    if (h2) {
      section = (h2[1] ?? '').trim().toLowerCase();
      continue;
    }
    const sup = /^>\s*Supersedes:\s*`?([^`]*)`?\s*$/.exec(line);
    if (sup) {
      out.supersedes = (sup[1] ?? '').trim();
      continue;
    }
    if (section === 'decision') {
      rationale.push(line);
    } else if (section.startsWith('alternatives')) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('|') || /^\|\s*:?-/.test(trimmed)) continue;
      const [option = '', reason = ''] = splitTableRow(trimmed);
      if (option.toLowerCase() === 'option' && reason.toLowerCase().startsWith('why')) continue;
      if (option || reason) out.alternatives.push({ option, reasonRejected: reason });
    }
  }
  out.rationale = rationale.join('\n').trim();
  return out;
}
