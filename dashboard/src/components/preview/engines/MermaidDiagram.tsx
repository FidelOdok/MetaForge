/**
 * A ```mermaid fence, drawn (FORGE-706).
 *
 * The preview markdown parser has always kept the fence's language, but every
 * fenced block rendered as a `<pre>`, so a work product carrying a diagram
 * showed its source instead of the diagram — in the twin inspector, the
 * full-screen modal and the project rows alike, since all three dispatch
 * through `PreviewHost`.
 *
 * Two things are deliberate here:
 *
 * `mermaid` is imported dynamically. It is a large library and most documents
 * have no diagram in them, so it must not be in the chunk that renders prose.
 *
 * This is the one place in the preview stack that injects HTML, which the
 * parser otherwise refuses to do on purpose. The output is mermaid's, not the
 * document's, and `securityLevel: 'strict'` is what keeps it that way: mermaid
 * sanitises the SVG through DOMPurify, renders label text rather than label
 * markup, and ignores `click` directives. Do not relax it to 'loose' — work
 * product text is written by agents, and a diagram is not worth a script tag.
 */
import { useEffect, useState } from 'react';
import { useThemeStore } from '../../../store/theme-store';
import { PC } from '../tokens';

/** Mermaid needs literal colours; the console's tokens are CSS variables. */
function token(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function palette() {
  return {
    background: token('--mf-c-191b22', '#191b22'),
    mainBkg: token('--mf-c-282a30', '#282a30'),
    primaryColor: token('--mf-c-282a30', '#282a30'),
    primaryTextColor: token('--mf-c-e2e2eb', '#e2e2eb'),
    primaryBorderColor: token('--mf-c-9a9aaa', '#9a9aaa'),
    secondaryColor: token('--mf-c-191b22', '#191b22'),
    tertiaryColor: token('--mf-c-111319', '#111319'),
    nodeBorder: token('--mf-c-9a9aaa', '#9a9aaa'),
    clusterBkg: token('--mf-c-111319', '#111319'),
    clusterBorder: token('--mf-c-9a9aaa', '#9a9aaa'),
    lineColor: token('--mf-c-86cfff', '#86cfff'),
    textColor: token('--mf-c-e2e2eb', '#e2e2eb'),
    titleColor: token('--mf-c-e2e2eb', '#e2e2eb'),
    edgeLabelBackground: token('--mf-c-191b22', '#191b22'),
    fontFamily: '"DM Sans", Arial, sans-serif',
    fontSize: '13px',
  };
}

/** Ids must be unique per render; mermaid builds DOM ids out of them. */
let seq = 0;

export function MermaidDiagram({ code }: { code: string }) {
  // Re-draw on a theme flip: the colours are baked into the SVG.
  const resolvedTheme = useThemeStore((s) => s.resolvedTheme);
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setFailed(false);

    void (async () => {
      try {
        const { default: mermaid } = await import('mermaid');
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          // Without this, a diagram that will not parse leaves mermaid's own
          // "syntax error" graphic -- a 2412x512 drawing of a bomb -- appended
          // to `document.body`, outside React's tree and outside anything that
          // would clean it up. Found by measuring the rendered page; the unit
          // test cannot see it, because it mocks mermaid away.
          suppressErrorRendering: true,
          theme: 'base',
          fontFamily: '"DM Sans", Arial, sans-serif',
          themeVariables: palette(),
        });
        const rendered = await mermaid.render(`mf-preview-diagram-${seq++}`, code);
        if (!cancelled) setSvg(rendered.svg);
      } catch {
        // A diagram that will not parse is a document problem the reader can
        // see for themselves, so fall back to the source rather than an
        // error nobody can act on.
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code, resolvedTheme]);

  if (failed) {
    return (
      <div data-testid="preview-mermaid" data-state="failed" style={{ margin: '0 0 0.9em' }}>
        <div className="font-mono" style={{ fontSize: 10, color: PC.amber, marginBottom: 4 }}>
          This diagram could not be drawn; showing its source.
        </div>
        <pre
          className="font-mono"
          style={{
            margin: 0,
            padding: 12,
            fontSize: 11,
            lineHeight: 1.55,
            background: PC.surfaceLow,
            border: `1px solid ${PC.border}`,
            borderRadius: 6,
            overflowX: 'auto',
          }}
        >
          {code}
        </pre>
      </div>
    );
  }

  if (svg === null) {
    return (
      <div
        data-testid="preview-mermaid"
        data-state="loading"
        className="font-mono"
        style={{ margin: '0 0 0.9em', fontSize: 11, color: PC.onSurfaceVariant }}
      >
        Drawing the diagram…
      </div>
    );
  }

  return (
    <div
      data-testid="preview-mermaid"
      data-state="drawn"
      role="img"
      aria-label="Diagram"
      style={{
        margin: '0 0 0.9em',
        padding: 12,
        background: PC.surfaceLow,
        border: `1px solid ${PC.border}`,
        borderRadius: 6,
        overflowX: 'auto',
      }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
