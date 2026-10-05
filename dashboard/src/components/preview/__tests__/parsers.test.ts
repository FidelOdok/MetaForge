import { describe, it, expect } from 'vitest';
import { parseInline, parseMarkdown, safeHref, splitTableRow } from '../parsers/markdown';
import { parseCsv, sniffDelimiter } from '../parsers/csv';
import { parseConstraintSet, parseDecision } from '../parsers/records';
import { tokenize } from '../parsers/highlight';
import { dxfToSvg } from '../parsers/dxfToSvg';
import { gerberToSvg, hastToSvg } from '../parsers/gerberToSvg';

describe('parseMarkdown', () => {
  it('parses headings, paragraphs, lists, code, quotes, rules and tables', () => {
    const blocks = parseMarkdown(
      [
        '# Title',
        '',
        'Para one',
        'continues.',
        '',
        '- a',
        '  - nested',
        '- [x] done',
        '',
        '1. first',
        '',
        '```c',
        'int x = 1;',
        '```',
        '',
        '> quoted',
        '',
        '---',
        '',
        '| A | B |',
        '|---|---|',
        '| 1 | 2 \\| 3 |',
      ].join('\n'),
    );
    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'paragraph', 'list', 'list', 'code', 'quote', 'rule', 'table']);
    expect(blocks[1]).toEqual({ kind: 'paragraph', text: 'Para one continues.' });
    expect(blocks[2]).toMatchObject({ ordered: false, items: [{ text: 'a', depth: 0 }, { text: 'nested', depth: 1 }, { text: 'done', checked: true }] });
    expect(blocks[3]).toMatchObject({ ordered: true });
    expect(blocks[4]).toEqual({ kind: 'code', lang: 'c', text: 'int x = 1;' });
    expect(blocks[7]).toEqual({ kind: 'table', header: ['A', 'B'], rows: [['1', '2 | 3']] });
  });

  it('survives an unterminated fence', () => {
    expect(parseMarkdown('```\nopen')).toEqual([{ kind: 'code', lang: '', text: 'open' }]);
  });

  it('parses inline code, strong, em and links', () => {
    expect(parseInline('a `b` **c** *d* [e](https://x.io)')).toEqual([
      { kind: 'text', text: 'a ' },
      { kind: 'code', text: 'b' },
      { kind: 'text', text: ' ' },
      { kind: 'strong', children: [{ kind: 'text', text: 'c' }] },
      { kind: 'text', text: ' ' },
      { kind: 'em', children: [{ kind: 'text', text: 'd' }] },
      { kind: 'text', text: ' ' },
      { kind: 'link', href: 'https://x.io', children: [{ kind: 'text', text: 'e' }] },
    ]);
  });

  it('drops script-capable link schemes', () => {
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref('data:text/html,x')).toBeNull();
    expect(safeHref('docs/page.md')).toBe('docs/page.md');
    expect(parseInline('[x](javascript:alert(1))')).toEqual([{ kind: 'text', text: 'x' }, { kind: 'text', text: ')' }]);
  });

  it('splits table rows with escaped pipes', () => {
    expect(splitTableRow('| a | b\\|c |')).toEqual(['a', 'b|c']);
  });
});

describe('parseCsv', () => {
  it('handles quotes, doubled quotes and embedded newlines', () => {
    const t = parseCsv('ref,desc,qty\nR1,"10k, 1%",2\nC1,"say ""hi""\nthere",1\n');
    expect(t.header).toEqual(['ref', 'desc', 'qty']);
    expect(t.rows).toEqual([
      ['R1', '10k, 1%', '2'],
      ['C1', 'say "hi"\nthere', '1'],
    ]);
  });
  it('pads short rows, strips a BOM and skips blank lines', () => {
    const t = parseCsv('﻿a,b\n\n1\n');
    expect(t).toEqual({ header: ['a', 'b'], rows: [['1', '']] });
  });
  it('sniffs tab and semicolon delimiters', () => {
    expect(sniffDelimiter('a\tb\tc')).toBe('\t');
    expect(sniffDelimiter('a;b;c')).toBe(';');
    expect(sniffDelimiter('single')).toBe(',');
  });
  it('is empty for an empty file', () => {
    expect(parseCsv('')).toEqual({ header: [], rows: [] });
  });
});

const CONSTRAINT_MD = [
  '# Constraint set: Power budget',
  '',
  '## max_current (error, electronics)',
  'Board draw stays under budget.',
  '**Acceptance criteria:** below 2 A',
  '**Verification method:** bench test',
  '**Binding:** current_a <= 2.0A on pcb',
  '```python',
  'ctx.current_a <= 2.0',
  '```',
  '',
  '## mass (warning, mechanical)',
  '```python',
  'ctx.mass_g < 250',
  '```',
].join('\n');

describe('parseConstraintSet', () => {
  it('reads the constraint recorder markdown into rows', () => {
    const { title, rows } = parseConstraintSet(CONSTRAINT_MD);
    expect(title).toBe('Power budget');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      name: 'max_current',
      severity: 'error',
      domain: 'electronics',
      message: 'Board draw stays under budget.',
      acceptanceCriteria: 'below 2 A',
      verificationMethod: 'bench test',
      binding: 'current_a <= 2.0A on pcb',
      expression: 'ctx.current_a <= 2.0',
    });
    expect(rows[1]).toMatchObject({ name: 'mass', severity: 'warning', expression: 'ctx.mass_g < 250' });
  });
  it('returns no rows for prose', () => {
    expect(parseConstraintSet('# Notes\n\nJust text.').rows).toEqual([]);
  });
});

describe('parseDecision', () => {
  it('reads the decision recorder markdown', () => {
    const d = parseDecision(
      [
        '# Use STM32F4',
        '',
        '> Supersedes: `dec-1`',
        '',
        '## Decision',
        '',
        'Enough timers and **FPU**.',
        '',
        '## Alternatives considered',
        '',
        '| Option | Why rejected |',
        '|---|---|',
        '| RP2040 | No FPU |',
        '| ESP32 | Pipe \\| in reason |',
      ].join('\n'),
    );
    expect(d).toEqual({
      title: 'Use STM32F4',
      supersedes: 'dec-1',
      rationale: 'Enough timers and **FPU**.',
      alternatives: [
        { option: 'RP2040', reasonRejected: 'No FPU' },
        { option: 'ESP32', reasonRejected: 'Pipe | in reason' },
      ],
    });
  });
});

describe('tokenize', () => {
  it('round-trips the source exactly', () => {
    const src = '#include <stdio.h>\n/* c */ int main(void) { return 0x1F; } // end\nchar *s = "a\\"b";\n';
    expect(tokenize(src, 'c').map((t) => t.text).join('')).toBe(src);
  });
  it('classifies C tokens', () => {
    const kinds = tokenize('#define X 1\nint y = 42; // hi', 'c').filter((t) => t.kind !== 'plain');
    expect(kinds.map((t) => [t.kind, t.text])).toEqual([
      ['preproc', '#define X 1'],
      ['type', 'int'],
      ['number', '42'],
      ['comment', '// hi'],
    ]);
  });
  it('classifies Python tokens, including triple-quoted strings', () => {
    const toks = tokenize('def f():\n    """doc\nmore"""\n    return None  # x', 'python').filter((t) => t.kind !== 'plain');
    expect(toks.map((t) => t.kind)).toEqual(['keyword', 'string', 'keyword', 'keyword', 'comment']);
  });
});

const DXF = [
  '0', 'SECTION', '2', 'ENTITIES',
  '0', 'LINE', '8', '0', '10', '0', '20', '0', '11', '10', '21', '5',
  '0', 'CIRCLE', '8', '0', '10', '5', '20', '5', '40', '2',
  '0', 'ARC', '8', '0', '10', '0', '20', '0', '40', '3', '50', '0', '51', '90',
  '0', 'LWPOLYLINE', '8', '0', '90', '3', '70', '1', '10', '0', '20', '0', '10', '4', '20', '0', '42', '1', '10', '4', '20', '4',
  '0', 'TEXT', '8', '0', '10', '1', '20', '1', '40', '2', '1', 'A<B',
  '0', 'ENDSEC', '0', 'EOF', '',
].join('\n');

describe('dxfToSvg', () => {
  it('draws lines, circles, arcs, bulged polylines and text', () => {
    const r = dxfToSvg(DXF);
    expect(r.entityCount).toBe(5);
    expect(r.svg.startsWith('<svg')).toBe(true);
    expect(r.svg).toContain('<circle cx="5" cy="-5" r="2"');
    expect((r.svg.match(/<path /g) ?? []).length).toBe(3);
    // Text is escaped, never markup.
    expect(r.svg).toContain('A&lt;B');
    expect(r.svg).not.toContain('<B');
  });
  it('reports an empty drawing', () => {
    const r = dxfToSvg(['0', 'SECTION', '2', 'ENTITIES', '0', 'ENDSEC', '0', 'EOF', ''].join('\n'));
    expect(r.svg).toBe('');
    expect(r.entityCount).toBe(0);
  });
});

describe('gerberToSvg', () => {
  const GERBER = ['%FSLAX26Y26*%', '%MOMM*%', '%ADD10C,0.5*%', 'D10*', 'X0Y0D02*', 'X10000000Y0D01*', 'X10000000Y5000000D01*', 'M02*'].join('\n');
  it('renders a layer to an SVG string with real attribute names', () => {
    const r = gerberToSvg(GERBER);
    expect(r.empty).toBe(false);
    expect(r.svg.startsWith('<svg')).toBe(true);
    expect(r.svg).toContain('viewBox="');
    expect(r.svg).toContain('stroke-linecap="round"');
    expect(r.svg).toContain('xmlns:xlink=');
  });
  it('flags an empty layer', () => {
    expect(gerberToSvg('%FSLAX26Y26*%\n%MOMM*%\nM02*').empty).toBe(true);
  });
  it('escapes text and attribute values', () => {
    expect(hastToSvg({ type: 'element', tagName: 'g', properties: { id: 'a"b' }, children: [{ type: 'text', value: '<x>' }] })).toBe(
      '<g id="a&quot;b">&lt;x&gt;</g>',
    );
  });
});
