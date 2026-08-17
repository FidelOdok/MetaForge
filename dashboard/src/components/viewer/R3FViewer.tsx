import { Suspense, useCallback, useEffect, useRef } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Environment, Grid, GizmoHelper, GizmoViewcube } from '@react-three/drei';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { RotateCcw } from 'lucide-react';
import { useViewerStore } from '../../store/viewer-store';
import { useThemeStore } from '../../store/theme-store';
import { useTransientTransform } from '../../store/transient-transform-store';
import type { TransformMode, Vec3 } from '../../store/transient-transform-store';
import { useSynthesizeConstraint } from '../../hooks/use-constraint-synthesis';
import type { DeltaTransform } from '../../api/endpoints/constraint';
import { useToast } from '../ui/Toast';
import { ErrorBoundary } from '../ErrorBoundary';
import { SceneContents } from './SceneContents';
import { BooleanCutPanel } from './BooleanCutPanel';
import type { PartInfo } from '../../types/viewer';

const RAD_TO_DEG = 180 / Math.PI;

// Tinkercad's translucent-red hole preview (MET-612).
const CUTTER_OVERLAY_TINT = { color: '#ff3b30', opacity: 0.35 };

const MODE_LABELS: Record<TransformMode, string> = {
  translate: 'MOVE',
  rotate: 'ROTATE',
  scale: 'SCALE',
};

/** Axis (x/y/z) and value of the component farthest from `baseline`. */
function dominantAxis(v: Vec3, baseline: number): { axis: 'x' | 'y' | 'z'; value: number } {
  const candidates: Array<{ axis: 'x' | 'y' | 'z'; value: number }> = [
    { axis: 'x', value: v[0] },
    { axis: 'y', value: v[1] },
    { axis: 'z', value: v[2] },
  ];
  return candidates.reduce((best, c) =>
    Math.abs(c.value - baseline) > Math.abs(best.value - baseline) ? c : best,
  );
}

/** Live numeric readout text for the active gizmo mode (e.g. "12.4mm", "34.2°", "120%"). */
function formatReadout(mode: TransformMode, delta: Vec3, rotationDelta: Vec3, scaleDelta: Vec3): string {
  if (mode === 'translate') {
    const mag = Math.sqrt(delta[0] ** 2 + delta[1] ** 2 + delta[2] ** 2);
    return `${mag.toFixed(1)}mm`;
  }
  if (mode === 'rotate') {
    const { value } = dominantAxis(rotationDelta, 0);
    return `${(value * RAD_TO_DEG).toFixed(1)}°`;
  }
  const { value } = dominantAxis(scaleDelta, 1);
  return `${Math.round(value * 100)}%`;
}

interface R3FViewerProps {
  onPartClick?: (part: PartInfo) => void;
  /** Called after a successful boolean-cut with the new node's id, so the
   * host page can pivot its own "selected node" state (MET-612). */
  onBooleanCutComplete?: (newNodeId: string) => void;
}

// Kinetic Console palette (see project_kinetic_console_design) — this file
// predates that token set living anywhere shared/importable, so the handful
// of colors the ViewCube needs are declared locally rather than reaching
// into TwinViewerPage's private KC object.
const VIEWCUBE_COLOR = '#282a30'; // surface-high
const VIEWCUBE_HOVER_COLOR = '#e67e22'; // primary-container (brand orange)
const VIEWCUBE_TEXT_COLOR = '#e2e2eb'; // on-surface
const VIEWCUBE_STROKE_COLOR = 'rgba(65,72,90,0.4)';

function LoadingFallback() {
  return (
    <mesh>
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial color="#888" wireframe />
    </mesh>
  );
}

// Isometric-ish viewing direction, pre-normalized (1, 0.75, 1) — a fixed
// angle looks natural regardless of model size; only the DISTANCE scales
// with modelBounds.radius below.
const VIEW_DIR = { x: 0.6247, y: 0.4685, z: 0.6247 };
// Distance-to-radius ratio that comfortably fits a bounding sphere in a 45°
// FOV camera with headroom (a tighter ratio clips corners on a tilted view).
const FIT_DISTANCE_FACTOR = 2.2;
// Generic framing used only before any model has reported its bounds (e.g.
// the very first frame, or an empty scene) — matches the previous hardcoded
// camera/target so behavior is unchanged until real bounds are available.
const DEFAULT_BOUNDS = { center: [0, 0, 0] as [number, number, number], radius: 36 };

/** Registers a camera-reset callback in the store so the button outside Canvas
 * can trigger it, and fits the camera to the loaded model's actual size and
 * position — a fixed generic framing regardless of what's loaded (MET-620)
 * could put the orbit pivot nowhere near the model, which reads as the ground
 * grid rotating rather than the camera orbiting the object. */
function CameraController() {
  const { camera } = useThree();
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const registerCameraReset = useViewerStore((s) => s.registerCameraReset);
  const modelBounds = useViewerStore((s) => s.modelBounds);

  const fitToModel = useCallback(() => {
    const { center, radius } = modelBounds ?? DEFAULT_BOUNDS;
    const dist = radius * FIT_DISTANCE_FACTOR;
    camera.position.set(
      center[0] + VIEW_DIR.x * dist,
      center[1] + VIEW_DIR.y * dist,
      center[2] + VIEW_DIR.z * dist,
    );
    camera.lookAt(center[0], center[1], center[2]);
    if (controlsRef.current) {
      controlsRef.current.target.set(center[0], center[1], center[2]);
      controlsRef.current.update();
    }
  }, [camera, modelBounds]);

  useEffect(() => {
    registerCameraReset(fitToModel);
  }, [registerCameraReset, fitToModel]);

  // Auto-fit whenever a new model's bounds become available — initial load,
  // or switching to a different work product — so the camera doesn't stay on
  // whatever generic framing was there before this model existed.
  useEffect(() => {
    if (modelBounds) fitToModel();
    // fitToModel already depends on modelBounds; re-running on its own
    // change (not fitToModel's identity) is exactly "a new model loaded".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelBounds]);

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      enableDamping
      dampingFactor={0.1}
    />
  );
}

/**
 * Mode switcher / Apply / Revert overlay for rigid-group manipulation
 * (MET-519, extended MET-611 with rotate/scale + a live numeric readout).
 * HTML overlay (outside the Canvas) shown while a group is selected — reads
 * the store directly so the readout updates on every drag tick without a
 * useFrame bridge. Apply posts the active mode's delta to constraint
 * synthesis; Revert discards all three pending deltas.
 */
function GizmoControls() {
  const selectedGroup = useTransientTransform((s) => s.selectedGroup);
  const mode = useTransientTransform((s) => s.mode);
  const setMode = useTransientTransform((s) => s.setMode);
  const delta = useTransientTransform((s) => s.delta);
  const rotationDelta = useTransientTransform((s) => s.rotationDelta);
  const scaleDelta = useTransientTransform((s) => s.scaleDelta);
  const isDirty = useTransientTransform((s) => s.isDirty);
  const revert = useTransientTransform((s) => s.revert);
  const clearAfterApply = useTransientTransform((s) => s.clearAfterApply);
  const selectGroup = useTransientTransform((s) => s.selectGroup);
  // selectedMeshName (highlight tint, status bar, BOM panel) lives in the
  // OTHER store and is set independently by SceneContents' click handler —
  // clearing only selectGroup left the mesh looking selected after "deselect"
  // (MET-618 follow-up: live-verified the gizmo cleared but the highlight and
  // status bar didn't).
  const selectPart = useViewerStore((s) => s.selectPart);
  const synth = useSynthesizeConstraint();
  const toast = useToast();

  const deselect = () => {
    selectGroup(null);
    selectPart(null);
  };

  // Escape deselects (and discards any pending delta, same as clicking empty
  // canvas) — previously the ONLY way out of a selection was Apply, since
  // Revert deliberately keeps the selection (MET-618).
  useEffect(() => {
    if (!selectedGroup) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') deselect();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedGroup, selectGroup, selectPart]);

  if (!selectedGroup) return null;

  const onApply = () => {
    let payload: DeltaTransform;
    if (mode === 'translate') {
      payload = { dx: delta[0], dy: delta[1], dz: delta[2] };
    } else if (mode === 'rotate') {
      const { axis, value } = dominantAxis(rotationDelta, 0);
      payload = { dx: 0, dy: 0, dz: 0, rotation: { axis, angle_deg: value * RAD_TO_DEG } };
    } else {
      const { axis, value } = dominantAxis(scaleDelta, 1);
      payload = { dx: 0, dy: 0, dz: 0, scale: { axis, factor: value } };
    }

    synth.mutate(
      { groupName: selectedGroup, delta: payload },
      {
        onSuccess: (res) => {
          if (res.status === 'conflict') {
            toast.error(res.conflict_reason ?? 'Constraint conflict — change rejected');
            revert();
          } else if (res.status === 'noop') {
            toast.info('No change to apply');
          } else {
            toast.success(res.suggestion);
            clearAfterApply();
          }
        },
        onError: () => toast.error('Apply failed — constraint synthesis unavailable'),
      },
    );
  };

  return (
    <div className="absolute left-1/2 top-3 z-50 -translate-x-1/2 flex flex-col gap-2 rounded-md bg-black/60 px-3 py-2 text-xs text-white/90 select-none">
      <div className="font-mono flex items-center gap-2">
        <span>{selectedGroup}</span>
        {isDirty && (
          <span className="text-amber-400">
            • {formatReadout(mode, delta, rotationDelta, scaleDelta)}
          </span>
        )}
        <button
          type="button"
          onClick={deselect}
          title="Deselect (Esc)"
          aria-label="Deselect"
          className="ml-1 rounded px-1 text-white/50 hover:bg-white/10 hover:text-white/90"
        >
          ×
        </button>
      </div>
      <div className="flex gap-1 rounded border border-white/10 p-0.5">
        {(Object.keys(MODE_LABELS) as TransformMode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`flex-1 rounded px-2 py-1 font-mono text-[10px] transition-colors ${
              mode === m
                ? 'bg-orange-600 text-white'
                : 'text-white/60 hover:bg-white/10 hover:text-white/90'
            }`}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onApply}
          disabled={!isDirty || synth.isPending}
          className="rounded bg-orange-600 px-2.5 py-1 font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {synth.isPending ? 'Applying…' : 'Apply'}
        </button>
        <button
          type="button"
          onClick={revert}
          disabled={!isDirty}
          className="rounded border border-white/20 px-2.5 py-1 text-white/90 transition-colors hover:bg-white/10 disabled:opacity-40"
        >
          Revert
        </button>
      </div>
    </div>
  );
}

export function R3FViewer({ onPartClick, onBooleanCutComplete }: R3FViewerProps) {
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const manifest = useViewerStore((s) => s.manifest);
  const resetCamera = useViewerStore((s) => s.resetCamera);
  const booleanCut = useViewerStore((s) => s.booleanCut);
  const selectPart = useViewerStore((s) => s.selectPart);
  const modelBounds = useViewerStore((s) => s.modelBounds);
  const themeMode = useThemeStore((s) => s.mode);
  const selectGroup = useTransientTransform((s) => s.selectGroup);

  const isDark =
    themeMode === 'dark' ||
    (themeMode === 'system' &&
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);

  const bgColor = isDark ? '#18181b' : '#f4f4f5';

  // Size and position the ground plane to the loaded model instead of a
  // fixed [0,-0.5,0]/200-unit grid that could sit nowhere near a small or
  // off-center model — the mismatch is exactly what made an orbit look like
  // the grid was the thing rotating (MET-620). Grid is the ONLY ground
  // surface (no separate ContactShadows quad) — Tinkercad's floor is a
  // single plane too; stacking two coplanar surfaces at this same position
  // was a latent z-fighting bug that MET-620's larger draw distances made
  // clearly visible (MET-621).
  const groundY = (modelBounds?.groundY ?? 0) - 0.5;
  const modelRadius = modelBounds?.radius ?? 36;
  const gridSize = Math.max(20, modelRadius * 6);
  // Cell/section spacing scale with the model too — the original 5/25 values
  // were sized for roughly this reference radius, so this keeps that same
  // ratio (unchanged look for a model near that size) instead of a tiny part
  // getting comically coarse grid cells or a huge one getting illegibly fine ones.
  const gridScale = modelRadius / 36;

  if (!glbUrl || !manifest) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-zinc-400">
        Upload a STEP file or load a model to view it in 3D
      </div>
    );
  }

  return (
    <div className="twin-canvas relative h-full w-full">
      <Canvas
        camera={{ position: [80, 60, 80], fov: 45, near: 0.1, far: 10000 }}
        gl={{ preserveDrawingBuffer: true }}
        style={{ background: bgColor }}
        // A click that doesn't hit any mesh (empty grid/background) deselects
        // the active group — the same drei/r3f idiom used for click-to-select.
        // A drag-to-orbit never fires this (no `click` without movement), so
        // it can't be triggered by accident while navigating the camera.
        // Clears BOTH stores — selectGroup (the gizmo) and selectPart (the
        // mesh highlight tint + status bar + BOM panel), otherwise the mesh
        // still reads as selected even once the gizmo is gone.
        onPointerMissed={() => {
          selectGroup(null);
          selectPart(null);
        }}
      >
        <Suspense fallback={<LoadingFallback />}>
          <SceneContents
            glbUrl={glbUrl}
            manifest={manifest}
            onPartClick={onPartClick}
          />
        </Suspense>

        {/* Boolean-cut cutter preview (MET-612) — translucent red, same Canvas,
            identity transform (v1 scope: both models authored already
            positioned relative to the target's origin). */}
        {booleanCut.cutterGlbUrl && booleanCut.cutterManifest && (
          <Suspense fallback={null}>
            <SceneContents
              glbUrl={booleanCut.cutterGlbUrl}
              manifest={booleanCut.cutterManifest}
              overlayTint={CUTTER_OVERLAY_TINT}
            />
          </Suspense>
        )}

        <CameraController />

        {/* ViewCube — click a face/edge/corner to snap the camera to that
            orientation, or drag to orbit manually (Tinkercad-style nav). */}
        <GizmoHelper alignment="top-right" margin={[64, 64]}>
          <GizmoViewcube
            color={VIEWCUBE_COLOR}
            hoverColor={VIEWCUBE_HOVER_COLOR}
            textColor={VIEWCUBE_TEXT_COLOR}
            strokeColor={VIEWCUBE_STROKE_COLOR}
          />
        </GizmoHelper>

        {/* Environment fetches its HDRI from a third-party CDN at render
            time — a network blip there is cosmetic (IBL reflections only,
            the ambientLight/directionalLight below already light the scene)
            and must never crash the whole viewer via the page's ErrorBoundary
            (MET-622). Suspense + a local ErrorBoundary contain the failure. */}
        <ErrorBoundary fallback={null}>
          <Suspense fallback={null}>
            <Environment preset="studio" />
          </Suspense>
        </ErrorBoundary>
        <Grid
          args={[gridSize, gridSize]}
          position={[modelBounds?.center[0] ?? 0, groundY, modelBounds?.center[2] ?? 0]}
          cellSize={5 * gridScale}
          cellThickness={0.5}
          cellColor={isDark ? '#333' : '#ddd'}
          sectionSize={25 * gridScale}
          sectionThickness={1}
          sectionColor={isDark ? '#555' : '#bbb'}
          fadeDistance={gridSize}
          infiniteGrid
        />

        <ambientLight intensity={0.4} />
        <directionalLight position={[50, 50, 25]} intensity={0.8} />
      </Canvas>

      {/* Rigid-group Apply/Revert overlay (MET-519) */}
      <GizmoControls />

      {/* Boolean-cut panel (MET-612) */}
      <BooleanCutPanel onCutComplete={onBooleanCutComplete} />

      {/* Controls hint overlay — bottom-14 clears the 32px twin status bar */}
      <div className="absolute bottom-14 left-3 z-10 rounded-md bg-black/50 px-3 py-1.5 text-xs text-white/80 select-none pointer-events-none">
        Left drag: rotate · Scroll: zoom · Right drag: pan · Click a part to move it
      </div>

      {/* Camera reset button — bottom-right (bottom-14 clears the twin status
          bar, matching the controls-hint overlay) so it doesn't sit on top of
          the ViewCube anchored top-right. */}
      <button
        type="button"
        onClick={resetCamera}
        className="absolute right-3 bottom-14 z-30 flex items-center gap-1.5 rounded-md bg-black/50 px-2.5 py-1.5 text-xs text-white/80 transition-colors hover:bg-black/70 hover:text-white"
        title="Reset camera to default view"
        aria-label="Reset camera"
      >
        <RotateCcw size={12} />
        Reset view
      </button>
    </div>
  );
}
