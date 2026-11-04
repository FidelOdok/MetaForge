import { Fragment, type ReactNode } from 'react';

// A small Markdown renderer for agent replies: paragraphs, headings, bullet
// and numbered lists, code blocks, **bold**, *italic*, `code` and links.
// It builds React elements and never sets HTML, so reply text cannot inject
// markup; links are limited to http(s) and open in a new tab.

const INLINE =
  /(\[(?:work_product|node):\/\/[0-9A-Za-z-]+\]|\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
// A citation of a twin record, as the agent writes it: [work_product://<id>].
const CITATION = /^\[(?:work_product|node):\/\/([0-9A-Za-z-]+)\]$/;

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

function safeHref(href: string): string | null {
  return /^https?:\/\//i.test(href) ? href : null;
}

export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const token = match[0];
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = `${at}`;
    const cited = CITATION.exec(token);
    if (cited) {
      // A raw id means nothing to a reader; a small "source" link opens the record in the twin.
      out.push(
        <a key={key} className="mb-cite" href={`/twin?node=${encodeURIComponent(cited[1]!)}`} title={`Twin record ${cited[1]}`}>
          source
        </a>,
      );
    } else if (token.startsWith('**')) out.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith('`')) out.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (token.startsWith('[')) {
      const [, label, href] = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token) ?? [];
      const safe = href ? safeHref(href) : null;
      out.push(
        safe ? (
          <a key={key} href={safe} target="_blank" rel="noreferrer noopener">
            {label}
          </a>
        ) : (
          label ?? token
        ),
      );
    } else out.push(<em key={key}>{token.slice(1, -1)}</em>);
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Chunk =
  | { kind: 'p'; lines: string[] }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'code'; text: string };

const FENCE = '```';
const HEADING = /^(#{1,4})\s+(.*)$/;

function startsBlock(line: string): boolean {
  return line.startsWith(FENCE) || HEADING.test(line) || BULLET.test(line) || NUMBERED.test(line);
}

function chunks(source: string): Chunk[] {
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const at = (k: number): string => lines[k] ?? '';
  const out: Chunk[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = at(i);
    if (line.startsWith(FENCE)) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !at(i).startsWith(FENCE)) body.push(at(i++));
      i += 1;
      out.push({ kind: 'code', text: body.join('\n') });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ kind: 'h', level: heading[1]?.length ?? 1, text: heading[2] ?? '' });
      i += 1;
      continue;
    }
    const list = BULLET.test(line) ? BULLET : NUMBERED.test(line) ? NUMBERED : null;
    if (list) {
      const items: string[] = [];
      while (i < lines.length && list.test(at(i))) items.push(list.exec(at(i++))?.[1] ?? '');
      out.push(list === BULLET ? { kind: 'ul', items } : { kind: 'ol', items });
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && at(i).trim() && !startsBlock(at(i))) para.push(at(i++));
    out.push({ kind: 'p', lines: para });
  }
  return out;
}

function renderChunk(c: Chunk, key: number): ReactNode {
  switch (c.kind) {
    case 'code':
      return (
        <pre key={key}>
          <code>{c.text}</code>
        </pre>
      );
    case 'h': {
      const Tag = `h${Math.min(c.level + 2, 6)}` as 'h3' | 'h4' | 'h5' | 'h6';
      return <Tag key={key}>{renderInline(c.text)}</Tag>;
    }
    case 'ul':
    case 'ol': {
      const List = c.kind;
      return (
        <List key={key}>
          {c.items.map((item, k) => (
            <li key={k}>{renderInline(item)}</li>
          ))}
        </List>
      );
    }
    case 'p':
      return (
        <p key={key}>
          {c.lines.map((l, k) => (
            <Fragment key={k}>
              {k > 0 && <br />}
              {renderInline(l)}
            </Fragment>
          ))}
        </p>
      );
  }
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  return <div className={`mb-markdown ${className ?? ''}`}>{chunks(text).map(renderChunk)}</div>;
}
