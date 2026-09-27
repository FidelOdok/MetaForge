import React from 'react';
import CodeBlock from '@theme-original/CodeBlock';

/**
 * Wraps every fenced block in the console's labelled code card.
 *
 * The label is the one piece of information a bare code block never carries:
 * whether you are looking at a shell you paste into a terminal, a request
 * body, or a config file. The console's docs put it in a header bar, so this
 * does too — derived from the fence language unless the author wrote an
 * explicit `title=`.
 *
 * The copy button is deliberately *not* reimplemented. Docusaurus's own button
 * is still rendered (CSS lifts it into the header bar), which keeps its
 * clipboard fallbacks, its aria-live announcement and its i18n.
 */

const LABELS = {
  bash: 'Terminal',
  sh: 'Terminal',
  shell: 'Terminal',
  zsh: 'Terminal',
  console: 'Terminal',
  text: 'Output',
  plaintext: 'Output',
  json: 'JSON',
  jsonc: 'JSON',
  yaml: 'YAML',
  yml: 'YAML',
  toml: 'TOML',
  python: 'Python',
  py: 'Python',
  ts: 'TypeScript',
  tsx: 'TypeScript',
  js: 'JavaScript',
  jsx: 'JavaScript',
  http: 'HTTP',
  sql: 'SQL',
  cypher: 'Cypher',
  diff: 'Diff',
  docker: 'Dockerfile',
  dockerfile: 'Dockerfile',
  mermaid: 'Diagram',
};

function languageOf(className) {
  const match = /(?:^|\s)language-([\w-]+)/.exec(className ?? '');
  return match ? match[1].toLowerCase() : null;
}

/** Terminal glyph for a shell block, a file glyph for everything else. */
function Glyph({ terminal }) {
  const paths = terminal
    ? ['M4 17l6-6-6-6', 'M12 19h8']
    : ['M4 22h14a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v4', 'M14 2v4a2 2 0 0 0 2 2h4', 'm5 12-3 3 3 3', 'm9 18 3-3-3-3'];
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

export default function CodeBlockWrapper(props) {
  const language = languageOf(props.className);
  const source = typeof props.children === 'string' ? props.children : '';

  // Mermaid renders a diagram, not code — leave it alone entirely.
  if (language === 'mermaid') {
    return <CodeBlock {...props} />;
  }

  // Inline code and language-less one-liners get no card: a header bar reading
  // "Code" above a single word is noise.
  if (!language && !props.title && !source.includes('\n')) {
    return <CodeBlock {...props} />;
  }

  const terminal = ['bash', 'sh', 'shell', 'zsh', 'console'].includes(language ?? '');
  const label = props.title ?? LABELS[language ?? ''] ?? (language ? language.toUpperCase() : 'Code');
  const multiline = source.trim().includes('\n');

  return (
    <div className="dx-code">
      <header>
        <Glyph terminal={terminal} />
        <span>{label}</span>
      </header>
      <CodeBlock {...props} title={undefined} showLineNumbers={props.showLineNumbers ?? multiline} />
    </div>
  );
}
