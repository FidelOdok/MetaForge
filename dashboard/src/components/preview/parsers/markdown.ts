/**
 * A small, dependency-free Markdown block parser for work product previews
 * (PRDs, documentation, decision records, constraint sets).
 *
 * It returns a plain AST that `MarkdownView` turns into React elements, so
 * nothing is ever injected as HTML: raw HTML in the source renders as text.
 * Covers what MetaForge's own recorders emit plus common GitHub-flavoured
 * Markdown: headings, paragraphs, fenced code, block quotes, rules, flat or
 * indented lists, task items and pipe tables.
 */

export type MdBlock =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'quote'; blocks: MdBlock[] }
  | { kind: 'rule' }
  | { kind: 'list'; ordered: boolean; items: MdListItem[] }
  | { kind: 'table'; header: string[]; rows: string[][] };

export interface MdListItem {
  text: string;
  depth: number;
  checked?: boolean;
}

const FENCE = /^\s*(```+|~~~+)\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

/** Split a pipe-table row into cells, honouring `\|` escapes. */
export function splitTableRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = '';
  for (let i = 0; i < s.length; i++) {
    const ch = s[i] ?? '';
    if (ch === '\\' && s[i + 1] === '|') {
      cur += '|';
      i++;
    } else if (ch === '|') {
      cells.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  cells.push(cur.trim());
  return cells;
}

export function parseMarkdown(source: string): MdBlock[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const L = (k: number): string => lines[k] ?? '';
  const blocks: MdBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = L(i);

    if (line.trim() === '') {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1] ?? '```';
      const body: string[] = [];
      i++;
      while (i < lines.length && !L(i).trim().startsWith(marker)) {
        body.push(L(i));
        i++;
      }
      i++; // closing fence (or EOF)
      blocks.push({ kind: 'code', lang: (fence[2] ?? '').toLowerCase(), text: body.join('\n') });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: 'heading', level: (heading[1] ?? '#').length, text: heading[2] ?? '' });
      i++;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ kind: 'rule' });
      i++;
      continue;
    }

    if (line.trimStart().startsWith('>')) {
      const inner: string[] = [];
      while (i < lines.length && L(i).trimStart().startsWith('>')) {
        inner.push(L(i).trimStart().replace(/^>\s?/, ''));
        i++;
      }
      blocks.push({ kind: 'quote', blocks: parseMarkdown(inner.join('\n')) });
      continue;
    }

    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(L(i + 1)) && L(i + 1).includes('-')) {
      const header = splitTableRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && L(i).includes('|') && L(i).trim() !== '') {
        const cells = splitTableRow(L(i));
        while (cells.length < header.length) cells.push('');
        rows.push(cells.slice(0, header.length));
        i++;
      }
      blocks.push({ kind: 'table', header, rows });
      continue;
    }

    const first = LIST_ITEM.exec(line);
    if (first) {
      const ordered = /\d/.test(first[2] ?? '');
      const items: MdListItem[] = [];
      const baseIndent = (first[1] ?? '').length;
      while (i < lines.length) {
        const m = LIST_ITEM.exec(L(i));
        if (m) {
          let text = m[3] ?? '';
          let checked: boolean | undefined;
          const task = /^\[( |x|X)\]\s+(.*)$/.exec(text);
          if (task) {
            checked = task[1] !== ' ';
            text = task[2] ?? '';
          }
          const depth = Math.max(0, Math.floor(((m[1] ?? '').length - baseIndent) / 2));
          items.push({ text, depth, checked });
          i++;
        } else if (L(i).trim() !== '' && /^\s+/.test(L(i)) && items.length > 0) {
          // Lazy continuation of the previous item.
          const last = items[items.length - 1];
          if (last) last.text += ` ${L(i).trim()}`;
          i++;
        } else {
          break;
        }
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }

    // Paragraph: runs until a blank line or the start of another block.
    const para: string[] = [];
    while (
      i < lines.length &&
      L(i).trim() !== '' &&
      !FENCE.test(L(i)) &&
      !HEADING.test(L(i)) &&
      !LIST_ITEM.test(L(i)) &&
      !L(i).trimStart().startsWith('>') &&
      !(para.length > 0 && RULE.test(L(i)))
    ) {
      para.push(L(i).trim());
      i++;
    }
    if (para.length === 0) {
      // A line no rule above claimed; keep it as text rather than loop.
      para.push(L(i).trim());
      i++;
    }
    blocks.push({ kind: 'paragraph', text: para.join(' ') });
  }

  return blocks;
}

export type MdInline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; children: MdInline[] }
  | { kind: 'em'; children: MdInline[] }
  | { kind: 'link'; href: string; children: MdInline[] };

const INLINE = /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)|\*\*([^*]+?)\*\*|__([^_]+?)__|\*([^*\s][^*]*?)\*|(?<![\w])_([^_\s][^_]*?)_(?![\w])|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/;

/** Only http(s), mailto and relative links survive; anything else is text. */
export function safeHref(href: string): string | null {
  const h = href.trim();
  if (/^(https?:|mailto:)/i.test(h)) return h;
  if (/^[a-z][a-z0-9+.-]*:/i.test(h)) return null; // javascript:, data:, ...
  return h;
}

export function parseInline(text: string): MdInline[] {
  const out: MdInline[] = [];
  let rest = text;
  while (rest.length > 0) {
    const m = INLINE.exec(rest);
    if (!m) {
      out.push({ kind: 'text', text: rest });
      break;
    }
    if (m.index > 0) out.push({ kind: 'text', text: rest.slice(0, m.index) });
    if (m[1]) out.push({ kind: 'code', text: (m[2] ?? '').trim() });
    else if (m[3] !== undefined || m[4] !== undefined) out.push({ kind: 'strong', children: parseInline(m[3] ?? m[4] ?? '') });
    else if (m[5] !== undefined || m[6] !== undefined) out.push({ kind: 'em', children: parseInline(m[5] ?? m[6] ?? '') });
    else if (m[7] !== undefined) {
      const href = safeHref(m[8] ?? '');
      if (href) out.push({ kind: 'link', href, children: parseInline(m[7]) });
      else out.push({ kind: 'text', text: m[7] });
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}
