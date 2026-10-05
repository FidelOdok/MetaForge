import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree, type ThreeEvent } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import type { FieldMarker, FieldPeak, FieldQuantity, SimFieldPayload } from '../../types/simulationField';
import type { CameraSync } from './cameraSync';
import {
  autoDeformScale,
  availableQuantities,
  defaultQuantity,
  deformedPositions,
  fieldRange,
  formatFieldValue,
  legendGradient,
  probeValue,
  vertexColors,
  type Range,
} from './fieldMath';

// Kinetic Console tokens (same values as TwinViewerPage's local KC).
const KC = {
  surfaceHigh: 'var(--mf-c-282a30)',
  border: 'var(--mf-r-65-72-90-0p2)',
  borderMid: 'var(--mf-r-65-72-90-0p3)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  glass: 'rgba(30,31,38,0.90)',
  accent: '#e67e22',
} as const;

const FIXTURE_COLOR = '#3b82f6'; // blue support symbol, as in FacePickerViewer
const LOAD_COLOR = '#ff5a0a'; // Kinetic Console primary-container orange
const PEAK_COLOR = '#ffffff';

const MARKER_LABEL: Record<FieldMarker['kind'], string> = {
  fixture: 'Fixture',
  load: 'Load',
  heat_source: 'Heat source',
  sink: 'Sink',
};

export interface FieldViewerProps {
  payload: SimFieldPayload;
  /** Controlled quantity; uncontrolled (defaults to stress or temperature) when omitted. */
  quantity?: FieldQuantity;
  onQuantityChange?: (quantity: FieldQuantity) => void;
  /** Legend range override, e.g. one shared range for a side-by-side compare. */
  range?: Range;
  /** Controlled deformation scale; uncontrolled (auto) when omitted. */
  deformScale?: number;
  onDeformScaleChange?: (scale: number) => void;
  /** Keeps this viewer's camera in step with others on the same sync. */
  cameraSync?: CameraSync;
  syncId?: string;
  height?: number;
  title?: string;
  /** Hide the quantity / scale toolbar (a compare drives both viewers from one). */
  showControls?: boolean;
}

interface Probe {
  value: number;
}

/**
 * FORGE-532: the 3D result field of one simulation_result. Colour map of the
 * selected quantity (von Mises stress, displacement magnitude or
 * temperature) on the solved mesh's outer surface, a legend with the
 * full-field min/max, the deformed shape under a scale-factor slider, load
 * and fixture markers where the run recorded them, the peak location, and a
 * hover probe of the value under the cursor.
 *
 * Exported as the viewer a preview registry can mount as its 'sim' engine
 * (see SimFieldPreview); this component itself takes an already-fetched
 * payload and does no I/O.
 */
export function FieldViewer({
  payload,
  quantity: quantityProp,
  onQuantityChange,
  range: rangeProp,
  deformScale: deformProp,
  onDeformScaleChange,
  cameraSync,
  syncId = 'field-viewer',
  height = 360,
  title,
  showControls = true,
}: FieldViewerProps) {
  const quantities = useMemo(() => availableQuantities(payload), [payload]);
  const [quantityState, setQuantityState] = useState<FieldQuantity | null>(() => defaultQuantity(payload));
  const quantity = quantityProp && payload.fields[quantityProp] ? quantityProp : quantityState;
  const autoScale = useMemo(() => autoDeformScale(payload), [payload]);
  const [deformState, setDeformState] = useState<number>(autoScale);
  const deformScale = deformProp ?? deformState;
  const [showMarkers, setShowMarkers] = useState(true);
  const [probe, setProbe] = useState<Probe | null>(null);

  const field = quantity ? payload.fields[quantity] : undefined;
  const range = rangeProp ?? (quantity ? fieldRange(payload, quantity) : null);
  const canDeform = payload.displacement !== null && autoScale > 0;
  const sliderMax = Math.max(autoScale * 4, 1);

  const setQuantity = (q: FieldQuantity) => {
    setQuantityState(q);
    onQuantityChange?.(q);
  };
  const setDeform = (s: number) => {
    setDeformState(s);
    onDeformScaleChange?.(s);
  };

  if (!field || !range || !quantity) {
    return (
      <div data-testid="field-viewer-empty" style={{ fontSize: 11, color: KC.onSurfaceVariant }}>
        This result field has no stress, displacement or temperature values to draw.
      </div>
    );
  }

  return (
    <div
      data-testid="field-viewer"
      className="relative"
      style={{
        height,
        borderRadius: 8,
        overflow: 'hidden',
        background: '#18181b',
        border: `1px solid ${KC.borderMid}`,
      }}
    >
      <Canvas
        camera={initialCamera(payload)}
        onPointerMissed={() => setProbe(null)}
      >
        <FieldScene
          payload={payload}
          values={field.values}
          peak={field.peak}
          range={range}
          deformScale={canDeform ? deformScale : 0}
          showMarkers={showMarkers}
          onProbe={setProbe}
          cameraSync={cameraSync}
          syncId={syncId}
        />
      </Canvas>

      {/* Hover probe: top-left pill, as in the digital-twin studio spec. */}
      <div
        data-testid="field-probe"
        className="absolute top-2 left-2 px-3 py-1 rounded-full font-mono"
        style={{ fontSize: 11, color: KC.onSurface, background: KC.glass, backdropFilter: 'blur(8px)' }}
      >
        {title ? `${title} · ` : ''}
        {probe ? `${field.label}: ${formatFieldValue(probe.value, field.unit)}` : field.label}
      </div>

      <FieldLegend
        label={field.label}
        unit={field.unit}
        range={range}
        peak={field.peak?.value ?? null}
        markers={showMarkers ? payload.markers : []}
      />

      {payload.decimation.applied && (
        <div
          className="absolute bottom-12 left-2 font-mono"
          style={{ fontSize: 9, color: KC.onSurfaceVariant }}
          title="The surface was simplified to fit the stored field's size cap. The legend and peak use the full field."
        >
          Simplified from {payload.decimation.source_triangle_count.toLocaleString()} triangles
        </div>
      )}

      {showControls && (
        <div
          className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full px-3 py-1.5 flex items-center gap-3"
          style={{ background: KC.glass, backdropFilter: 'blur(12px)', border: `1px solid ${KC.border}` }}
        >
          <select
            aria-label="Field quantity"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value as FieldQuantity)}
            className="font-mono"
            style={{ fontSize: 11, background: 'transparent', color: KC.onSurface, border: 'none' }}
          >
            {quantities.map((q) => (
              <option key={q} value={q} style={{ background: '#1e1f26' }}>
                {payload.fields[q]?.label ?? q}
              </option>
            ))}
          </select>
          {canDeform && (
            <>
              <span className="w-px h-4" style={{ background: '#33343b' }} />
              <span className="font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant }}>
                Deform
              </span>
              <input
                type="range"
                aria-label="Deformation scale"
                min={0}
                max={sliderMax}
                step={sliderMax / 100}
                value={deformScale}
                onChange={(e) => setDeform(Number(e.target.value))}
                className="w-20 h-1 cursor-pointer"
                style={{ accentColor: KC.accent }}
              />
              <span
                data-testid="deform-scale"
                className="font-mono"
                style={{ fontSize: 11, color: KC.accent, minWidth: 40 }}
              >
                ×{formatScale(deformScale)}
              </span>
            </>
          )}
          {payload.markers.length > 0 && (
            <>
              <span className="w-px h-4" style={{ background: '#33343b' }} />
              <button
                type="button"
                aria-pressed={showMarkers}
                onClick={() => setShowMarkers((v) => !v)}
                title="Show load and fixture markers"
                className="rounded px-1.5"
                style={{
                  color: showMarkers ? KC.accent : KC.onSurfaceVariant,
                  background: showMarkers ? 'rgba(230,126,34,0.15)' : 'transparent',
                }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: 16, verticalAlign: 'middle' }}>
                  push_pin
                </span>
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function formatScale(scale: number): string {
  if (scale === 0) return '0';
  return scale >= 10 ? Math.round(scale).toLocaleString() : Number(scale.toPrecision(2)).toString();
}

/** Vertical legend: gradient bar with max at the top and min at the bottom,
 * the peak value, and a text key for any markers (identity is never colour
 * alone). */
export function FieldLegend({
  label,
  unit,
  range,
  peak,
  markers,
}: {
  label: string;
  unit: string;
  range: Range;
  peak: number | null;
  markers: FieldMarker[];
}) {
  return (
    <div
      data-testid="field-legend"
      className="absolute top-2 right-2 rounded-md px-2 py-2 flex flex-col gap-1"
      style={{ background: KC.glass, backdropFilter: 'blur(8px)', border: `1px solid ${KC.border}`, maxWidth: 150 }}
    >
      <div className="font-mono uppercase" style={{ fontSize: 9, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
        {label} ({unit})
      </div>
      <div className="flex gap-2 items-stretch">
        <div
          aria-hidden
          style={{ width: 10, height: 110, borderRadius: 2, background: legendGradient() }}
        />
        <div className="flex flex-col justify-between font-mono" style={{ fontSize: 10, color: KC.onSurface }}>
          <span data-testid="legend-max">{formatFieldValue(range.max, unit)}</span>
          <span style={{ color: KC.onSurfaceVariant }}>{formatFieldValue((range.min + range.max) / 2, unit)}</span>
          <span data-testid="legend-min">{formatFieldValue(range.min, unit)}</span>
        </div>
      </div>
      {peak !== null && (
        <div className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
          <span style={{ color: PEAK_COLOR }}>●</span> Peak {formatFieldValue(peak, unit)}
        </div>
      )}
      {markers.map((m, i) => (
        <div key={`${m.kind}-${m.label}-${i}`} className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
          <span style={{ color: m.kind === 'fixture' || m.kind === 'sink' ? FIXTURE_COLOR : LOAD_COLOR }}>■</span>{' '}
          {MARKER_LABEL[m.kind] ?? m.kind} {m.label}
          {markerDetail(m)}
        </div>
      ))}
    </div>
  );
}

function markerDetail(m: FieldMarker): string {
  if (m.vector) {
    const magnitude = Math.hypot(m.vector[0], m.vector[1], m.vector[2]);
    return ` · ${Number(magnitude.toPrecision(4))} ${m.unit ?? 'N'}`;
  }
  if (m.value !== undefined) return ` · ${m.value} ${m.unit ?? ''}`.trimEnd();
  return '';
}

function bboxInfo(payload: SimFieldPayload): { center: THREE.Vector3; diag: number } {
  const min = new THREE.Vector3(...payload.bbox.min);
  const max = new THREE.Vector3(...payload.bbox.max);
  return { center: min.clone().add(max).multiplyScalar(0.5), diag: Math.max(min.distanceTo(max), 1e-6) };
}

function initialCamera(payload: SimFieldPayload) {
  const { center, diag } = bboxInfo(payload);
  const d = diag * 1.3;
  return {
    position: [center.x + d * 0.7, center.y + d * 0.55, center.z + d * 0.7] as [number, number, number],
    fov: 40,
    near: diag / 1000,
    far: diag * 100,
  };
}

function FieldScene({
  payload,
  values,
  peak,
  range,
  deformScale,
  showMarkers,
  onProbe,
  cameraSync,
  syncId,
}: {
  payload: SimFieldPayload;
  values: number[];
  peak: FieldPeak | null;
  range: Range;
  deformScale: number;
  showMarkers: boolean;
  onProbe: (probe: Probe | null) => void;
  cameraSync?: CameraSync;
  syncId: string;
}) {
  const { center, diag } = useMemo(() => bboxInfo(payload), [payload]);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(Float32Array.from(payload.positions), 3));
    g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(payload.positions.length), 3));
    g.setIndex(payload.indices);
    return g;
  }, [payload]);
  useEffect(() => () => geometry.dispose(), [geometry]);

  useEffect(() => {
    const attr = geometry.getAttribute('position') as THREE.BufferAttribute;
    attr.array.set(deformedPositions(payload, deformScale));
    attr.needsUpdate = true;
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
  }, [geometry, payload, deformScale]);

  useEffect(() => {
    const attr = geometry.getAttribute('color') as THREE.BufferAttribute;
    attr.array.set(vertexColors(values, range));
    attr.needsUpdate = true;
  }, [geometry, values, range]);

  const lastProbe = useRef<string>('');
  const handleMove = (e: ThreeEvent<PointerEvent>) => {
    if (!e.face) return;
    e.stopPropagation();
    const positions = (geometry.getAttribute('position') as THREE.BufferAttribute).array;
    const value = probeValue(positions, values, e.face.a, e.face.b, e.face.c, [e.point.x, e.point.y, e.point.z]);
    const key = value.toPrecision(4);
    if (key !== lastProbe.current) {
      lastProbe.current = key;
      onProbe({ value });
    }
  };

  const markerSize = diag * 0.02;
  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight position={[center.x + diag, center.y + diag * 1.5, center.z + diag]} intensity={0.9} />
      <directionalLight position={[center.x - diag, center.y - diag, center.z - diag]} intensity={0.3} />
      <OrbitControls makeDefault target={[center.x, center.y, center.z]} />
      {cameraSync && <CameraSyncBridge sync={cameraSync} id={syncId} />}
      <mesh
        geometry={geometry}
        onPointerMove={handleMove}
        onPointerOut={() => {
          lastProbe.current = '';
          onProbe(null);
        }}
      >
        <meshStandardMaterial vertexColors side={THREE.DoubleSide} roughness={0.65} metalness={0.05} />
      </mesh>
      {peak && (
        <mesh position={peak.position}>
          <sphereGeometry args={[markerSize * 0.6, 16, 16]} />
          <meshBasicMaterial color={PEAK_COLOR} />
        </mesh>
      )}
      {showMarkers && payload.markers.map((m, i) => <Marker key={`${m.kind}-${i}`} marker={m} size={markerSize} diag={diag} />)}
    </>
  );
}

function Marker({ marker, size, diag }: { marker: FieldMarker; size: number; diag: number }) {
  const arrow = useMemo(() => {
    if (marker.kind !== 'load' || !marker.vector) return null;
    const dir = new THREE.Vector3(...marker.vector);
    if (dir.lengthSq() < 1e-12) return null;
    dir.normalize();
    const length = diag * 0.18;
    // The tip sits on the loaded face: the arrow pushes into it.
    const origin = new THREE.Vector3(...marker.position).sub(dir.clone().multiplyScalar(length));
    return new THREE.ArrowHelper(dir, origin, length, LOAD_COLOR, length * 0.3, length * 0.18);
  }, [marker, diag]);

  if (marker.kind === 'fixture' && marker.bbox) {
    const min = new THREE.Vector3(...marker.bbox.min);
    const max = new THREE.Vector3(...marker.bbox.max);
    const dims = max.clone().sub(min);
    const mid = min.clone().add(max).multiplyScalar(0.5);
    const pad = size * 0.5;
    return (
      <mesh position={[mid.x, mid.y, mid.z]}>
        <boxGeometry args={[dims.x + pad, dims.y + pad, dims.z + pad]} />
        <meshBasicMaterial color={FIXTURE_COLOR} transparent opacity={0.35} wireframe />
      </mesh>
    );
  }
  if (arrow) return <primitive object={arrow} />;
  return (
    <mesh position={marker.position}>
      <sphereGeometry args={[size, 16, 16]} />
      <meshBasicMaterial color={marker.kind === 'sink' || marker.kind === 'fixture' ? FIXTURE_COLOR : LOAD_COLOR} />
    </mesh>
  );
}

interface ControlsLike {
  target: THREE.Vector3;
  update: () => void;
  addEventListener: (type: 'change', listener: () => void) => void;
  removeEventListener: (type: 'change', listener: () => void) => void;
}

/** Publishes this viewer's camera pose on every orbit and applies the poses
 * other viewers publish, guarding against the echo `controls.update()`
 * would otherwise send straight back. */
function CameraSyncBridge({ sync, id }: { sync: CameraSync; id: string }) {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as unknown as ControlsLike | null;
  const applying = useRef(false);

  useEffect(() => {
    if (!controls) return undefined;
    const onChange = () => {
      if (applying.current) return;
      sync.publish(
        {
          position: [camera.position.x, camera.position.y, camera.position.z],
          target: [controls.target.x, controls.target.y, controls.target.z],
        },
        id,
      );
    };
    controls.addEventListener('change', onChange);
    const unsubscribe = sync.subscribe((pose, source) => {
      if (source === id) return;
      applying.current = true;
      camera.position.set(...pose.position);
      controls.target.set(...pose.target);
      controls.update();
      applying.current = false;
    });
    return () => {
      controls.removeEventListener('change', onChange);
      unsubscribe();
    };
  }, [camera, controls, sync, id]);
  return null;
}

export default FieldViewer;
