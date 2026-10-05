/**
 * A tiny lexical highlighter for firmware and script previews (C/C++,
 * Python, and a generic fallback). Tokens only: comments, strings,
 * preprocessor lines, numbers, keywords. It never builds HTML; the code
 * engine maps tokens to styled spans.
 */

export type TokenKind = 'comment' | 'string' | 'keyword' | 'number' | 'preproc' | 'type' | 'plain';

export interface Token {
  kind: TokenKind;
  text: string;
}

const C_KEYWORDS = [
  'auto', 'break', 'case', 'const', 'continue', 'default', 'do', 'else', 'enum', 'extern', 'for', 'goto', 'if',
  'inline', 'register', 'return', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union', 'volatile', 'while',
  'class', 'namespace', 'template', 'typename', 'public', 'private', 'protected', 'virtual', 'override', 'new',
  'delete', 'this', 'using', 'constexpr', 'nullptr', 'true', 'false', 'NULL', 'try', 'catch', 'throw',
];
const C_TYPES = [
  'void', 'char', 'short', 'int', 'long', 'float', 'double', 'signed', 'unsigned', 'bool', 'size_t',
  'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t', 'int8_t', 'int16_t', 'int32_t', 'int64_t',
];
const PY_KEYWORDS = [
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del',
  'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal',
  'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield', 'self',
];
const PY_TYPES = ['int', 'float', 'str', 'bool', 'list', 'dict', 'tuple', 'set', 'bytes'];
const GENERIC_KEYWORDS = [
  'fn', 'let', 'mut', 'pub', 'impl', 'struct', 'enum', 'match', 'use', 'mod', 'const', 'var', 'function',
  'return', 'if', 'else', 'for', 'while', 'import', 'export', 'from', 'class', 'new', 'true', 'false', 'null',
];

interface LangSpec {
  keywords: Set<string>;
  types: Set<string>;
  lineComment: string;
  block: boolean;
  preproc: boolean;
}

function spec(language: string): LangSpec {
  if (language === 'python') {
    return { keywords: new Set(PY_KEYWORDS), types: new Set(PY_TYPES), lineComment: '#', block: false, preproc: false };
  }
  if (language === 'c' || language === 'cpp') {
    return { keywords: new Set(C_KEYWORDS), types: new Set(C_TYPES), lineComment: '//', block: true, preproc: true };
  }
  return { keywords: new Set(GENERIC_KEYWORDS), types: new Set(), lineComment: '//', block: true, preproc: false };
}

/** Tokenise source into a flat list; concatenating `text` reproduces it exactly. */
export function tokenize(source: string, language: string): Token[] {
  const s = spec(language);
  const out: Token[] = [];
  let i = 0;
  let plain = '';
  const flush = () => {
    if (plain) out.push({ kind: 'plain', text: plain });
    plain = '';
  };
  const atLineStart = () => {
    for (let j = i - 1; j >= 0; j--) {
      if (source[j] === '\n') return true;
      if (source[j] !== ' ' && source[j] !== '\t') return false;
    }
    return true;
  };

  while (i < source.length) {
    const ch = source[i] ?? '';
    const rest = source.startsWith(s.lineComment, i);
    if (rest) {
      flush();
      const end = source.indexOf('\n', i);
      const stop = end === -1 ? source.length : end;
      out.push({ kind: 'comment', text: source.slice(i, stop) });
      i = stop;
      continue;
    }
    if (s.block && source.startsWith('/*', i)) {
      flush();
      const end = source.indexOf('*/', i + 2);
      const stop = end === -1 ? source.length : end + 2;
      out.push({ kind: 'comment', text: source.slice(i, stop) });
      i = stop;
      continue;
    }
    if (s.preproc && ch === '#' && atLineStart()) {
      flush();
      const end = source.indexOf('\n', i);
      const stop = end === -1 ? source.length : end;
      out.push({ kind: 'preproc', text: source.slice(i, stop) });
      i = stop;
      continue;
    }
    if (ch === '"' || ch === "'" || (language === 'javascript' && ch === '`')) {
      flush();
      const triple = language === 'python' && source.startsWith(ch.repeat(3), i);
      const quote = triple ? ch.repeat(3) : ch;
      let j = i + quote.length;
      while (j < source.length) {
        if (source[j] === '\\') {
          j += 2;
          continue;
        }
        if (source.startsWith(quote, j)) {
          j += quote.length;
          break;
        }
        if (!triple && source[j] === '\n') break;
        j++;
      }
      out.push({ kind: 'string', text: source.slice(i, Math.min(j, source.length)) });
      i = Math.min(j, source.length);
      continue;
    }
    if (/[0-9]/.test(ch) && !/[\w]/.test(source[i - 1] ?? '')) {
      flush();
      const m = /^(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+\.?\d*(?:[eE][+-]?\d+)?)[uUlLfF]*/.exec(source.slice(i));
      const text = m?.[0] ?? ch;
      out.push({ kind: 'number', text });
      i += text.length;
      continue;
    }
    if (/[A-Za-z_]/.test(ch)) {
      const m = /^[A-Za-z_]\w*/.exec(source.slice(i));
      const word = m?.[0] ?? ch;
      if (s.keywords.has(word)) {
        flush();
        out.push({ kind: 'keyword', text: word });
      } else if (s.types.has(word)) {
        flush();
        out.push({ kind: 'type', text: word });
      } else {
        plain += word;
      }
      i += word.length;
      continue;
    }
    plain += ch;
    i++;
  }
  flush();
  return out;
}
