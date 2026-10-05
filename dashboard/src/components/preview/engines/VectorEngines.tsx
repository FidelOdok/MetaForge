import { useMemo } from 'react';
import { gerberToSvg } from '../parsers/gerberToSvg';
import { dxfToSvg } from '../parsers/dxfToSvg';
import { PC, type PreviewMode } from '../tokens';
import { PreviewUnavailable } from '../PreviewUnavailable';

/**
 * FORGE-531: 2D vector engines. Gerber/NC drill renders through tracespace,
 * DXF through dxf-parser plus our own SVG writer. Both produce an SVG string
 * shown via <img> from a data URL, so the file can never inject markup or
 * script into the page.
 */

function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function Drawing({ svg, alt, mode, testId, caption }: { svg: string; alt: string; mode: PreviewMode; testId: string; caption: string }) {
  return (
    <figure data-testid={testId} className="flex flex-col h-full" style={{ margin: 0 }}>
      <div className="flex-1 min-h-0 flex items-center justify-center rounded" style={{ background: 'var(--mf-c-0a0b0f)', border: `1px solid ${PC.border}`, padding: mode === 'compact' ? 8 : 20, minHeight: mode === 'modal' ? 0 : 200 }}>
        <img src={svgDataUrl(svg)} alt={alt} style={{ maxWidth: '100%', maxHeight: '100%', width: '100%', height: mode === 'modal' ? '100%' : 260, objectFit: 'contain' }} />
      </div>
      <figcaption className="font-mono mt-1" style={{ fontSize: 10, color: PC.onSurfaceVariant }}>{caption}</figcaption>
    </figure>
  );
}

export function GerberView({ source, name, format, mode, downloadUrl }: { source: string; name: string; format: string; mode: PreviewMode; downloadUrl: string }) {
  const result = useMemo(() => {
    try {
      return gerberToSvg(source);
    } catch {
      return null;
    }
  }, [source]);
  if (!result || result.empty) {
    return (
      <PreviewUnavailable
        engine="gerber"
        format={format}
        downloadUrl={downloadUrl}
        reason="The Gerber renderer could not draw this file. It may be a different format or an empty layer."
      />
    );
  }
  const kind = /^(drl|xln|exc)$/.test(format) ? 'NC drill' : 'Gerber layer';
  return <Drawing svg={result.svg} alt={name} mode={mode} testId="preview-gerber" caption={`${kind} · rendered by tracespace`} />;
}

export function DxfView({ source, name, format, mode, downloadUrl }: { source: string; name: string; format: string; mode: PreviewMode; downloadUrl: string }) {
  const result = useMemo(() => {
    try {
      return dxfToSvg(source);
    } catch {
      return null;
    }
  }, [source]);
  if (!result || !result.svg) {
    return (
      <PreviewUnavailable
        engine="dxf"
        format={format}
        downloadUrl={downloadUrl}
        reason="The DXF viewer found no drawable 2D entities in this file."
      />
    );
  }
  const skipped = result.skipped.length > 0 ? ` · not drawn: ${result.skipped.join(', ')}` : '';
  return <Drawing svg={result.svg} alt={name} mode={mode} testId="preview-dxf" caption={`${result.entityCount} entities${skipped}`} />;
}
