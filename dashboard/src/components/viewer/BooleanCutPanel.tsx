import { useMemo } from 'react';
import { useViewerStore } from '../../store/viewer-store';
import { useTwinNodes } from '../../hooks/use-twin';
import { useActiveProject } from '../../hooks/use-active-project';
import { useBooleanCut } from '../../hooks/use-boolean-cut';
import { getNodeModel } from '../../api/endpoints/twin';
import { useToast } from '../ui/Toast';
import type { ModelManifest } from '../../types/viewer';

// Kinetic Console palette (see project_kinetic_console_design) — this file
// predates that token set living anywhere shared/importable (same rationale
// as the ViewCube colors in R3FViewer.tsx), so the handful of colors this
// panel needs are declared locally.
const KC_SURFACE = 'rgba(30,31,38,0.92)';
const KC_BORDER = 'rgba(65,72,90,0.3)';
const KC_ON_SURFACE = '#e2e2eb';
const KC_ON_SURFACE_VARIANT = '#9a9aaa';
const KC_ORANGE = '#e67e22';

interface BooleanCutPanelProps {
  /** Called after a successful cut with the new node's id, so the host page
   * can pivot its own "selected node" state (detail panel, history, etc). */
  onCutComplete?: (newNodeId: string) => void;
}

/**
 * Bottom-center panel for the boolean-cut flow (MET-612): pick a sibling
 * cad_model node as the cutter, then Hole (subtract) or Group (union). Shown
 * whenever `viewerStore.booleanCut.mode !== 'idle'`.
 */
export function BooleanCutPanel({ onCutComplete }: BooleanCutPanelProps) {
  const booleanCut = useViewerStore((s) => s.booleanCut);
  const setBooleanCutCutter = useViewerStore((s) => s.setBooleanCutCutter);
  const clearBooleanCutCutter = useViewerStore((s) => s.clearBooleanCutCutter);
  const closeBooleanCut = useViewerStore((s) => s.closeBooleanCut);
  const setBooleanCutting = useViewerStore((s) => s.setBooleanCutting);
  const { activeProjectId } = useActiveProject();
  const { data: nodes } = useTwinNodes(activeProjectId ?? undefined);
  const cut = useBooleanCut();
  const toast = useToast();

  const siblings = useMemo(
    () =>
      (nodes ?? []).filter(
        (n) => n.properties.wp_type === 'cad_model' && n.id !== booleanCut.targetNodeId,
      ),
    [nodes, booleanCut.targetNodeId],
  );

  if (booleanCut.mode === 'idle' || !booleanCut.targetNodeId) return null;

  const onPickCutter = async (cutterNodeId: string) => {
    if (!cutterNodeId) {
      clearBooleanCutCutter();
      return;
    }
    try {
      const result = await getNodeModel(cutterNodeId);
      const manifest: ModelManifest = {
        parts: result.metadata.parts.map((p) => ({
          name: p.name,
          meshName: p.meshName ?? p.name,
          children: (p.children ?? []) as ModelManifest['parts'],
          boundingBox: p.boundingBox as ModelManifest['parts'][number]['boundingBox'],
        })),
        meshToNodeMap: {},
        materials: result.metadata.materials ?? [],
        stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
      };
      const glbUrl = result.glb_url.startsWith('/v1/') ? `/api${result.glb_url}` : result.glb_url;
      setBooleanCutCutter(cutterNodeId, glbUrl, manifest);
    } catch (err) {
      console.error('Failed to load cutter model:', err);
      toast.error('Could not load the selected cutter model');
    }
  };

  const onCut = (operation: 'subtract' | 'union') => {
    if (!booleanCut.targetNodeId || !booleanCut.cutterNodeId) return;
    setBooleanCutting(true);
    cut.mutate(
      {
        targetNodeId: booleanCut.targetNodeId,
        cutterNodeId: booleanCut.cutterNodeId,
        operation,
      },
      {
        onSuccess: (result) => {
          toast.success(
            `${operation === 'subtract' ? 'Hole' : 'Group'} committed — ${result.node.name}`,
          );
          onCutComplete?.(result.node.id);
        },
        onError: (err: unknown) => {
          const status = (err as { response?: { status?: number } })?.response?.status;
          if (status === 409) {
            toast.error('Cutter does not intersect the target — nothing was committed');
          } else if (status === 503) {
            toast.error('CAD adapter unavailable — boolean operation could not run');
          } else {
            toast.error('Boolean-cut failed');
          }
        },
        onSettled: () => setBooleanCutting(false),
      },
    );
  };

  return (
    <div
      className="absolute left-1/2 z-50 -translate-x-1/2 flex items-center gap-3 rounded-md px-3 py-2 text-xs select-none"
      style={{
        // 140px clears the Exploded-view controls panel above the 88px/56px
        // stack (TwinViewerPage's explode wrapper + R3FViewer's bottom-14
        // hint/reset row) — both can be visible at once while cutting.
        bottom: 150,
        background: KC_SURFACE,
        backdropFilter: 'blur(16px)',
        border: `1px solid ${KC_BORDER}`,
        color: KC_ON_SURFACE,
      }}
    >
      <span className="font-mono uppercase" style={{ letterSpacing: '0.05em', color: KC_ON_SURFACE_VARIANT }}>
        Boolean cut
      </span>

      <select
        value={booleanCut.cutterNodeId ?? ''}
        onChange={(e) => void onPickCutter(e.target.value)}
        className="font-mono rounded px-2 py-1"
        style={{ background: 'rgba(0,0,0,0.3)', border: `1px solid ${KC_BORDER}`, color: KC_ON_SURFACE }}
      >
        <option value="">Select cutter…</option>
        {siblings.map((n) => (
          <option key={n.id} value={n.id}>
            {n.name}
          </option>
        ))}
      </select>

      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={() => onCut('subtract')}
          disabled={booleanCut.mode !== 'ready' || booleanCut.cutting}
          className="rounded px-2.5 py-1 font-medium transition-opacity hover:opacity-90 disabled:opacity-40"
          style={{ background: '#c0392b', color: '#fff' }}
        >
          {booleanCut.cutting ? 'Cutting…' : 'Hole'}
        </button>
        <button
          type="button"
          onClick={() => onCut('union')}
          disabled={booleanCut.mode !== 'ready' || booleanCut.cutting}
          className="rounded px-2.5 py-1 font-medium transition-opacity hover:opacity-90 disabled:opacity-40"
          style={{ background: KC_ORANGE, color: '#fff' }}
        >
          {booleanCut.cutting ? 'Cutting…' : 'Group'}
        </button>
        <button
          type="button"
          onClick={closeBooleanCut}
          disabled={booleanCut.cutting}
          className="rounded border px-2.5 py-1 transition-colors hover:bg-white/10 disabled:opacity-40"
          style={{ borderColor: KC_BORDER, color: KC_ON_SURFACE_VARIANT }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
