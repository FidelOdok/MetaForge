import { VIEWER_COLOR_MANAGEMENT } from './viewerColor';
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { Environment, GizmoHelper, GizmoViewcube } from '@react-three/drei';
import { TinkercadControls } from '../../lib/TinkercadControls';
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

// MET-747: lazy so an ordinary CAD_MODEL viewer never pulls in
// urdf-loader/@dimforge/rapier3d-compat (same rationale AssemblyExportPanel's
// lazy UrdfPreviewPanel import already established).
const RobotSceneContents = lazy(() =>
  import('./RobotSceneContents').then((m) => ({ default: m.RobotSceneContents })),
);
const RobotControlsOverlay = lazy(() =>
  import('./RobotControlsOverlay').then((m) => ({ default: m.RobotControlsOverlay })),
);

const RAD_TO_DEG = 180 / Math.PI;

/** Pixels per workplane texture tile. One tile spans one major section. */
const GRID_TEXTURE_PX = 256;
/** Minor cells within a tile, i.e. cells per section (the old 25/5 ratio). */
const GRID_CELLS_PER_TILE = 5;

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
const VIEWCUBE_HOVER_COLOR = '#ff5a0a'; // primary-container (brand orange)
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

// Generic framing used only before any model has reported its bounds (e.g.
// the very first frame, or an empty scene) — matches the previous hardcoded
// camera/target so behavior is unchanged until real bounds are available.
const DEFAULT_BOUNDS = { center: [0, 0, 0] as [number, number, number], radius: 36 };

/**
 * MET-747: an invisible-except-for-its-shadow ground plane (a `shadowMaterial`
 * only ever renders the shadow cast onto it, never a visible surface of its
 * own) so the new shadow-casting directionalLight (below) has something real
 * to shadow onto -- previously there was no ground at all (the visible grid
 * stays disabled per MET-623's shimmer issue; this doesn't touch that).
 *
 * Positioned/sized from `modelBounds` when available (GLB path -- computed
 * once per load by SceneContents). The robot-description path never
 * computes modelBounds (RobotSceneContents has no equivalent), so this falls
 * back to a fixed offset guessed from the quadruped reference build's own
 * scale (~100mm legs) rather than a real per-robot bound -- an honest
 * approximation, not exact: worst case the catcher plane sits at a slightly
 * wrong height, which is cosmetic, not a functional bug.
 */
function GroundShadowCatcher() {
  const modelBounds = useViewerStore((s) => s.modelBounds);
  const center = modelBounds?.center ?? DEFAULT_BOUNDS.center;
  const radius = modelBounds?.radius ?? DEFAULT_BOUNDS.radius;
  const groundY = modelBounds?.groundY ?? -100;
  const size = Math.max(radius * 6, 200);

  return (
    <mesh position={[center[0], groundY, center[2]]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[size, size]} />
      <shadowMaterial opacity={0.28} />
    </mesh>
  );
}

/** One tile of the workplane, drawn once into a canvas and reused as a
 * repeating texture.
 *
 * The anti-aliasing is the entire point (FORGE-616). A mipmapped texture
 * averages the cell lines down as they recede, so a distant patch of floor
 * converges to a flat tint instead of a moire of half-hit lines crawling
 * about as the camera moves. The two approaches tried before had no such
 * averaging: drei's `Grid` evaluated its lines per fragment and anti-aliased
 * them with screen-space derivatives (FORGE-34), and bare `GridHelper`
 * segments are a `LineBasicMaterial` with no anti-aliasing at all
 * (FORGE-612). Both shimmered, for different reasons.
 *
 * Returns null where there is no 2D canvas context -- jsdom, mainly. The
 * caller then draws no floor, which is visibly wrong rather than quietly
 * wrong.
 */
export function createWorkplaneTexture(
  minorColor: string,
  majorColor: string,
  maxAnisotropy: number,
): THREE.Texture | null {
  const canvas = document.createElement('canvas');
  canvas.width = GRID_TEXTURE_PX;
  canvas.height = GRID_TEXTURE_PX;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const cells = GRID_CELLS_PER_TILE;
  const step = GRID_TEXTURE_PX / cells;

  ctx.clearRect(0, 0, GRID_TEXTURE_PX, GRID_TEXTURE_PX);
  ctx.strokeStyle = minorColor;
  ctx.lineWidth = 1;
  for (let i = 1; i < cells; i += 1) {
    const at = Math.round(i * step) + 0.5;
    ctx.beginPath();
    ctx.moveTo(at, 0);
    ctx.lineTo(at, GRID_TEXTURE_PX);
    ctx.moveTo(0, at);
    ctx.lineTo(GRID_TEXTURE_PX, at);
    ctx.stroke();
  }

  // The tile edges are the major lines: every tile boundary is one, so the
  // section grid falls out of the repeat instead of needing a second plane.
  ctx.strokeStyle = majorColor;
  ctx.lineWidth = 2;
  ctx.strokeRect(0, 0, GRID_TEXTURE_PX, GRID_TEXTURE_PX);

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  // These three lines are the fix. Mipmaps let a receding patch of floor
  // average its cells into a flat tint instead of aliasing into a crawling
  // moire, and anisotropy keeps that from smearing to mush at the grazing
  // angles a workplane is mostly seen from.
  texture.generateMipmaps = true;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.anisotropy = maxAnisotropy;
  return texture;
}

function useGridTexture(minorColor: string, majorColor: string): THREE.Texture | null {
  const { gl } = useThree();
  const maxAnisotropy = gl?.capabilities?.getMaxAnisotropy?.() ?? 1;

  return useMemo(
    () => createWorkplaneTexture(minorColor, majorColor, maxAnisotropy),
    [minorColor, majorColor, maxAnisotropy],
  );
}

/**
 * The visible workplane the model sits on, restored after FORGE-34/MET-623
 * hid it and re-done in FORGE-616 so it stops shimmering.
 *
 * Why it matters beyond decoration: with nothing under the model there is no
 * reference for the motion, so an orbit reads as the object wobbling in a void
 * rather than the view moving around a thing standing on a floor.
 *
 * It is one textured plane, not line geometry -- see `useGridTexture` for why
 * that is what makes it hold still.
 */
function GroundGrid({ isDark }: { isDark: boolean }) {
  const modelBounds = useViewerStore((s) => s.modelBounds);
  const texture = useGridTexture(
    isDark ? '#3a3a3a' : '#d8d8d8',
    isDark ? '#5a5a5a' : '#b4b4b4',
  );

  // Only the GLB path computes bounds. The robot-description path has none,
  // and a floor placed from a guess would cut straight through the robot, so
  // it keeps the no-grid behaviour it has always had rather than get a plane
  // at an invented height. (GroundShadowCatcher can guess safely: an
  // invisible plane at the wrong height costs a misplaced shadow, not a slab
  // through the model.)
  if (!modelBounds || !texture) return null;

  const { center, radius, groundY: modelGroundY } = modelBounds;
  // Just below the shadow catcher, so a cast shadow reads on top of the grid
  // rather than fighting it -- the same 0.5 offset the original grid used.
  const groundY = (modelGroundY ?? 0) - 0.5;

  // Size scales with the model: a fixed 200-unit grid sat nowhere near a small
  // or off-center model, which is what made an orbit look like the grid was
  // the thing rotating (MET-620). One texture tile spans a section, so the
  // repeat count is how many sections fit across the plane.
  const size = Math.max(20, radius * 6);
  const scale = radius / 36;
  const tiles = Math.min(64, Math.max(2, Math.round(size / (25 * scale))));
  texture.repeat.set(tiles, tiles);
  texture.needsUpdate = true;

  return (
    <mesh
      name="ground-grid"
      position={[center[0], groundY, center[2]]}
      rotation={[-Math.PI / 2, 0, 0]}
    >
      <planeGeometry args={[size, size]} />
      <meshBasicMaterial
        map={texture}
        transparent
        opacity={0.9}
        depthWrite={false}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

/** Registers a camera-reset callback in the store so the button outside Canvas
 * can trigger it, and fits the camera to the loaded model's actual size and
 * position — a fixed generic framing regardless of what's loaded (MET-620)
 * could put the orbit pivot nowhere near the model, which reads as the ground
 * grid rotating rather than the camera orbiting the object. */
function CameraController() {
  const { camera, scene, gl, set } = useThree();
  const controlsRef = useRef<TinkercadControls | null>(null);
  const registerCameraReset = useViewerStore((s) => s.registerCameraReset);
  const registerFocusPart = useViewerStore((s) => s.registerFocusPart);
  const registerOrbitControlsEnabledSetter = useViewerStore(
    (s) => s.registerOrbitControlsEnabledSetter,
  );
  const modelBounds = useViewerStore((s) => s.modelBounds);

  // FORGE-620: the Tinkercad turntable. It keeps orientation as azimuth,
  // elevation and radius and rebuilds the camera from them every frame, so
  // roll is structurally impossible and the workplane can never tilt -- the
  // property the arcball gave up and OrbitControls only approximated.
  //
  // upAxis is 'y' here, not Tinkercad's 'z': the workplane convention is the
  // same either way, but this viewer's GLB pipeline emits Y-up geometry, and
  // SceneContents already reads the model's floor off box.min.y.
  useEffect(() => {
    const controls = new TinkercadControls(camera as THREE.PerspectiveCamera, gl.domElement, {
      upAxis: 'y',
    });
    controlsRef.current = controls;
    // Stand in for drei's `makeDefault` so GizmoHelper and anything else
    // reading `state.controls` finds these. The cast is the same one drei
    // makes: r3f types `controls` as its own event-ful shape.
    set({ controls: controls as unknown as THREE.EventDispatcher });
    return () => {
      controls.dispose();
      controlsRef.current = null;
      set({ controls: null });
    };
  }, [camera, gl, set]);

  // The camera is derived state: nothing moves until update() rebuilds it from
  // the angles, and the damping eases toward whatever the last gesture asked
  // for. Priority -1 so this lands before anything that reads the camera.
  useFrame(() => controlsRef.current?.update(), -1);

  // FORGE-250: lets drag-to-pose suppress the camera the instant a joint drag
  // starts, same registration pattern as registerCameraReset below.
  useEffect(() => {
    registerOrbitControlsEnabledSetter((enabled) => {
      if (controlsRef.current) controlsRef.current.enabled = enabled;
    });
    return () => registerOrbitControlsEnabledSetter(null);
  }, [registerOrbitControlsEnabledSetter]);

  /** Frame a bounding box: the controls fit it by bounding sphere, which is
   * why no view direction is passed -- framing keeps the current angles. */
  const frameBox = useCallback((box: THREE.Box3) => {
    controlsRef.current?.frame(box);
  }, []);

  const fitToModel = useCallback(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    const { center, radius } = modelBounds ?? DEFAULT_BOUNDS;
    const c = new THREE.Vector3(center[0], center[1], center[2]);
    // modelBounds.radius is already the model's bounding-SPHERE radius, and
    // frame() re-derives a sphere from whatever box it is given. A cube of
    // +/-radius would have a sphere sqrt(3) bigger, pushing the camera 73%
    // too far out, so size the box so its own sphere comes back to radius.
    const side = (2 * radius) / Math.sqrt(3);
    // Reset view goes home as well as re-framing, so it recovers a known
    // orientation and not just a known distance.
    controls.setView('home');
    frameBox(new THREE.Box3().setFromCenterAndSize(c, new THREE.Vector3(side, side, side)));
  }, [frameBox, modelBounds]);

  /** Frame one part (FORGE-618). The pivot moves onto it, so it turns in
   * place instead of swinging through a wide arc. */
  const focusPart = useCallback(
    (meshName: string) => {
      const object = scene.getObjectByName(meshName);
      if (!object) return;
      const box = new THREE.Box3().setFromObject(object);
      if (box.isEmpty()) return;
      frameBox(box);
    },
    [frameBox, scene],
  );

  useEffect(() => {
    registerFocusPart(focusPart);
    return () => registerFocusPart(null);
  }, [registerFocusPart, focusPart]);

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

  return null;
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
  const modelFormat = useViewerStore((s) => s.modelFormat);
  const manifest = useViewerStore((s) => s.manifest);
  const resetCamera = useViewerStore((s) => s.resetCamera);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const focusPart = useViewerStore((s) => s.focusPart);
  const booleanCut = useViewerStore((s) => s.booleanCut);
  const selectPart = useViewerStore((s) => s.selectPart);
  const robotDescription = useViewerStore((s) => s.robotDescription);
  const themeMode = useThemeStore((s) => s.mode);
  const selectGroup = useTransientTransform((s) => s.selectGroup);

  // F frames the selected part and puts the orbit pivot on it (FORGE-618),
  // the same key Tinkercad and most CAD tools use for fit-to-selection.
  // Double-clicking a part in the scene does the same thing.
  useEffect(() => {
    if (!selectedMeshName) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'f' && e.key !== 'F') return;
      // Never steal the key from someone typing -- the twin page has a search
      // box and an agent chat on the same screen.
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      focusPart(selectedMeshName);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedMeshName, focusPart]);

  const isDark =
    themeMode === 'dark' ||
    (themeMode === 'system' &&
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);

  const bgColor = isDark ? '#18181b' : '#f4f4f5';

  if (!robotDescription && (!glbUrl || !manifest)) {
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
        gl={{
          preserveDrawingBuffer: true,
          ...VIEWER_COLOR_MANAGEMENT,
        }}
        // MET-747: enables the renderer's shadow map -- without this prop
        // castShadow/receiveShadow on the light and meshes below are no-ops.
        shadows
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
          {robotDescription ? (
            <RobotSceneContents nodeId={robotDescription.nodeId} />
          ) : (
            glbUrl &&
            manifest && (
              <SceneContents
                glbUrl={glbUrl}
                format={modelFormat}
                manifest={manifest}
                onPartClick={onPartClick}
              />
            )
          )}
        </Suspense>

        {/* Boolean-cut cutter preview (MET-612) — translucent red, same Canvas,
            identity transform (v1 scope: both models authored already
            positioned relative to the target's origin). Robot descriptions
            don't participate in boolean-cut, so no robotDescription guard
            needed here — booleanCut state is only ever populated from the
            CAD_MODEL "View 3D Model" flow. */}
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
        {/* The workplane the model stands on, restored in FORGE-612 after
            FORGE-34/MET-623 hid it: these are real GridHelper line segments,
            not drei Grid's derivative-shaded procedural lines, so the shimmer
            that forced the hiding cannot occur. The shadow-only catcher
            (MET-747) sits just above it and was never affected either -- a
            shadowMaterial has no lines to shimmer. */}
        <GroundGrid isDark={isDark} />
        <GroundShadowCatcher />

        <ambientLight intensity={0.4} />
        <directionalLight
          position={[50, 50, 25]}
          intensity={0.8}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-150}
          shadow-camera-right={150}
          shadow-camera-top={150}
          shadow-camera-bottom={-150}
          shadow-camera-near={1}
          shadow-camera-far={500}
          shadow-bias={-0.0005}
        />
      </Canvas>

      {/* Rigid-group Apply/Revert overlay (MET-519) */}
      <GizmoControls />

      {/* Boolean-cut panel (MET-612) */}
      <BooleanCutPanel onCutComplete={onBooleanCutComplete} />

      {/* Robot-description joint sliders + physics toggle (MET-747) — an
          HTML sibling of the Canvas, since it needs <input type="range">
          controls the Canvas itself can't render. */}
      {robotDescription && (
        <Suspense fallback={null}>
          <RobotControlsOverlay />
        </Suspense>
      )}

      {/* Controls hint overlay — bottom-20 clears both the 32px twin status
          bar and the page's bottom-left Sessions button (bottom:40 + 32px
          tall, spans 40-72px), which this overlay's old bottom-14 (56-84px)
          overlapped (MET-624). */}
      <div className="absolute bottom-20 left-3 z-10 rounded-md bg-black/50 px-3 py-1.5 text-xs text-white/80 select-none pointer-events-none">
        Right drag: rotate · Middle drag: pan · Scroll: zoom at cursor · Left click: select · Double-click or F: focus
      </div>

      {/* Camera reset button — bottom-right (bottom-20 clears the twin status
          bar and matches the controls-hint overlay's height, MET-624) so it
          doesn't sit on top of the ViewCube anchored top-right. */}
      <div className="absolute right-3 bottom-20 z-30 flex items-center gap-2">
        <button
          type="button"
          onClick={resetCamera}
          className="flex items-center gap-1.5 rounded-md bg-black/50 px-2.5 py-1.5 text-xs text-white/80 transition-colors hover:bg-black/70 hover:text-white"
          title="Reset camera to default view"
          aria-label="Reset camera"
        >
          <RotateCcw size={12} />
          Reset view
        </button>
      </div>
    </div>
  );
}
