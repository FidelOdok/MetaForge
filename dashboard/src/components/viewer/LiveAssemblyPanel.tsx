/**
 * Watch an assembly come together (FORGE-647).
 *
 * Opens over the twin viewer and shows each part as an agent places it with
 * `twin.place_part`. The list says what is placed and where; the scene draws
 * it. When the stream is not connected the panel says so, because an empty
 * assembly and a view that cannot see are not the same thing.
 */
import { lazy, Suspense } from 'react';
import { X } from 'lucide-react';

import { useLiveAssembly } from '../../hooks/use-live-assembly';

// Three and its loaders are heavy; they load only when a person opens the panel.
const LiveAssemblyScene = lazy(() => import('./LiveAssemblyScene'));

export interface LiveAssemblyPanelProps {
  projectId: string;
  onClose: () => void;
}

function fmt(v: [number, number, number]): string {
  return v.map((n) => String(Math.round(n * 100) / 100)).join(', ');
}

export function LiveAssemblyPanel({ projectId, onClose }: LiveAssemblyPanelProps) {
  const { state, connected } = useLiveAssembly(projectId);
  const parts = state.assemblies.flatMap((a) => a.parts);

  return (
    <section className="glass rounded" data-testid="live-assembly" aria-label="Live assembly">
      <header>
        <p className="eyebrow">LIVE ASSEMBLY</p>
        <button type="button" className="action-secondary" aria-label="Close live assembly" onClick={onClose}>
          <X size={16} />
        </button>
      </header>

      {!connected && (
        <p className="field-help" role="status" data-testid="live-assembly-offline">
          Not connected to the live stream, so nothing can be shown. This is not the same as nothing
          being built.
        </p>
      )}

      {connected && parts.length === 0 && (
        <p className="field-help" role="status" data-testid="live-assembly-empty">
          Nothing is being assembled. Parts appear here as an agent places them with twin.place_part.
        </p>
      )}

      {state.assemblies
        .filter((a) => a.parts.length > 0)
        .map((assembly) => (
          <div key={assembly.name}>
            <p className="eyebrow">{assembly.name.toUpperCase()}</p>
            <ol aria-label={`Parts of ${assembly.name}`}>
              {assembly.parts.map((p) => (
                <li key={p.nodeId}>
                  <strong>{p.name}</strong>{' '}
                  <span className="mono-value">
                    at {fmt(p.position)} mm, rotated {fmt(p.rotation)} deg
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ))}

      {parts.length > 0 && (
        <div style={{ height: 360 }}>
          <Suspense fallback={<p className="field-help">Loading the 3D view.</p>}>
            <LiveAssemblyScene parts={parts} />
          </Suspense>
        </div>
      )}
    </section>
  );
}
