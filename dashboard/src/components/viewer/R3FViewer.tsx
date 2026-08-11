import { Suspense, useEffect, useRef } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Environment, ContactShadows, Grid, GizmoHelper, GizmoViewcube } from '@react-three/drei';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { RotateCcw } from 'lucide-react';
import { useViewerStore } from '../../store/viewer-store';
import { useThemeStore } from '../../store/theme-store';
import { useTransientTransform } from '../../store/transient-transform-store';
import type { TransformMode, Vec3 } from '../../store/transient-transform-store';
import { useSynthesizeConstraint } from '../../hooks/use-constraint-synthesis';
import type { DeltaTransform } from '../../api/endpoints/constraint';
import { useToast } from '../ui/Toast';
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

/** Registers a camera-reset callback in the store so the button outside Canvas can trigger it. */
function CameraController() {
  const { camera } = useThree();
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const registerCameraReset = useViewerStore((s) => s.registerCameraReset);

  useEffect(() => {
    registerCameraReset(() => {
      camera.position.set(80, 60, 80);
      camera.lookAt(0, 0, 0);
      if (controlsRef.current) {
        controlsRef.current.target.set(0, 0, 0);
        controlsRef.current.update();
      }
    });
  }, [camera, registerCameraReset]);

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
  const synth = useSynthesizeConstraint();
  const toast = useToast();

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
  const themeMode = useThemeStore((s) => s.mode);

  const isDark =
    themeMode === 'dark' ||
    (themeMode === 'system' &&
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);

  const bgColor = isDark ? '#18181b' : '#f4f4f5';

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

        <Environment preset="studio" />
        <ContactShadows
          position={[0, -0.5, 0]}
          opacity={isDark ? 0.3 : 0.5}
          scale={100}
          blur={2}
        />
        <Grid
          args={[200, 200]}
          position={[0, -0.5, 0]}
          cellSize={5}
          cellThickness={0.5}
          cellColor={isDark ? '#333' : '#ddd'}
          sectionSize={25}
          sectionThickness={1}
          sectionColor={isDark ? '#555' : '#bbb'}
          fadeDistance={200}
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
