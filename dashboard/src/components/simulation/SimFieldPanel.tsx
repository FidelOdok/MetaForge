import { lazy, Suspense } from 'react';
import { useSimulationField } from '../../hooks/use-simulation-results';
import type { TwinNode } from '../../types/twin';

// The R3F viewer is split into its own chunk: the Sim page lists results
// without loading three.js until one is actually opened.
const FieldViewer = lazy(() => import('./FieldViewer'));

const MUTED = 'var(--mf-c-9a9aaa)';

export function FieldNote({ children, testId }: { children: React.ReactNode; testId: string }) {
  return (
    <div
      data-testid={testId}
      className="rounded px-3 py-2"
      style={{ fontSize: 11, color: MUTED, background: 'rgba(40,42,48,0.6)', border: '1px solid rgba(65,72,90,0.2)' }}
    >
      {children}
    </div>
  );
}

export const FIELD_NOT_STORED_TEXT =
  'Field not stored. This result was recorded without its 3D field (before fields were persisted, or the upload failed), so only the numbers are available.';

interface SimFieldPanelProps {
  resultId: string;
  /** From the result listing / node properties. `false` skips the fetch and
   * shows the note straight away; `undefined` (unknown) tries the fetch. */
  hasField?: boolean;
  height?: number;
  title?: string;
}

/** FORGE-532: fetches one result's 3D field and renders it, or the
 * "field not stored" note for a result that has none (every result
 * recorded before this ticket). */
export function SimFieldPanel({ resultId, hasField, height = 360, title }: SimFieldPanelProps) {
  const { data, isLoading, isError } = useSimulationField(resultId, hasField !== false);

  if (hasField === false || data?.status === 'not_stored') {
    return <FieldNote testId="field-not-stored">{FIELD_NOT_STORED_TEXT}</FieldNote>;
  }
  if (isLoading) {
    return (
      <div
        data-testid="field-loading"
        className="animate-pulse rounded-lg"
        style={{ height, background: 'rgba(40,42,48,0.4)' }}
      />
    );
  }
  if (isError || !data) {
    return <FieldNote testId="field-error">The 3D field could not be loaded. The numbers above are unaffected.</FieldNote>;
  }
  if (data.status === 'invalid') {
    return <FieldNote testId="field-error">The stored 3D field is not in a format this dashboard can draw.</FieldNote>;
  }
  return (
    <Suspense fallback={<div style={{ height }} />}>
      <FieldViewer payload={data.payload} height={height} title={title} />
    </Suspense>
  );
}

/** The viewer for a twin node, mounted by the FORGE-531 preview registry's
 * 'sim' engine (FeaSummaryCard): draws its field when it is a
 * simulation_result. */
export function SimFieldPreview({ node, height }: { node: TwinNode; height?: number }) {
  if (node.properties.wp_type !== 'simulation_result') return null;
  const stored = node.properties.field_stored;
  return (
    <SimFieldPanel
      resultId={node.id}
      hasField={typeof stored === 'boolean' ? stored : false}
      height={height}
    />
  );
}
