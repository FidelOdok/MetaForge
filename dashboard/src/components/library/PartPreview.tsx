import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { apiBase } from '../../lib/gatewayConfig';
import { VIEWER_COLOR_MANAGEMENT, VIEWER_GPU_PREFERENCE } from '../viewer/viewerColor';
import { centred, lookFor, type DrawShape } from '../../lib/libraryLook';
import { getComponentMesh, type LibraryComponent } from '../../api/endpoints/library';

interface AuthoredResult {
  parts: Array<{ shapes?: Array<{ name: string; positions: number[]; indices: number[] }>; error?: string }>;
}
interface AuthoredEngine {
  run: (parts: Array<{ id: string; name: string; kind: string; code: string }>, params: object) => Promise<AuthoredResult>;
}
declare global {
  interface Window {
    FGAuthored?: AuthoredEngine;
  }
}

let engineLoading: Promise<AuthoredEngine> | null = null;

/** The gateway's drawing engine (the same authored.js the Imagine studio runs), loaded once. */
export function loadEngine(): Promise<AuthoredEngine> {
  if (window.FGAuthored) return Promise.resolve(window.FGAuthored);
  if (!engineLoading) {
    engineLoading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = `${apiBase()}/library/engine/authored.js`;
      script.async = true;
      script.onload = () =>
        window.FGAuthored ? resolve(window.FGAuthored) : reject(new Error('the drawing engine did not load'));
      script.onerror = () => {
        engineLoading = null;
        reject(new Error('the drawing engine could not be fetched from the gateway'));
      };
      document.head.appendChild(script);
    });
  }
  return engineLoading;
}

/** The part's maker model when one is active, else nothing. */
export function activeMakerModel(part: LibraryComponent) {
  return (part.models || []).find((m) => m.engine === 'step' && (m.status ?? 'active') === 'active') ?? null;
}

function useShapes(part: LibraryComponent): { shapes: DrawShape[] | null; error: string | null; source: string } {
  const maker = activeMakerModel(part);
  // A refusal is an answer, not a failure: the gateway says why (no converter, a changed file).
  const mesh = useQuery({
    queryKey: ['library-mesh', part.name, part.version],
    queryFn: async () => {
      try {
        return { mesh: await getComponentMesh(part.name), error: null };
      } catch (exc) {
        const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        return { mesh: null, error: detail || "the maker's model could not be shown" };
      }
    },
    enabled: maker !== null,
    staleTime: Infinity,
    retry: false,
  });
  const [authored, setAuthored] = useState<{ shapes: DrawShape[] | null; error: string | null }>({
    shapes: null,
    error: null,
  });
  const code = maker ? '' : part.form_code || '';
  useEffect(() => {
    if (!code) return undefined;
    let live = true;
    setAuthored({ shapes: null, error: null });
    loadEngine()
      .then((engine) => engine.run([{ id: 'm', name: part.name, kind: 'component', code }], {}))
      .then((res) => {
        if (!live) return;
        const first = res.parts[0];
        if (!first || first.error) {
          setAuthored({ shapes: null, error: first?.error || 'the drawing code returned nothing' });
          return;
        }
        setAuthored({
          shapes: (first.shapes || []).map((s) => ({ ...s, look: lookFor(s.name) })),
          error: null,
        });
      })
      .catch((exc: Error) => live && setAuthored({ shapes: null, error: exc.message }));
    return () => {
      live = false;
    };
  }, [code, part.name]);

  if (maker) {
    const got = mesh.data?.mesh;
    return {
      shapes: got ? got.parts.map((p) => ({ ...p, look: lookFor(p.name, p.colour) })) : null,
      error: mesh.data?.error ?? null,
      source: 'maker',
    };
  }
  if (code) return { ...authored, source: 'drawn' };
  return { shapes: [], error: null, source: 'none' };
}

function ShapeMesh({ shape }: { shape: DrawShape }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(shape.positions, 3));
    g.setIndex(shape.indices);
    g.computeVertexNormals();
    return g;
  }, [shape]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  const { look } = shape;
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        color={look.color}
        roughness={Math.max(look.roughness, 0.35)}
        metalness={look.metalness * 0.55}
        transparent={look.opacity < 1}
        opacity={look.opacity}
        depthWrite={look.opacity >= 1}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

/** A library part in 3D: its maker's model when it has one, else its drawn model. */
export function PartPreview({ part }: { part: LibraryComponent }) {
  const { shapes, error, source } = useShapes(part);
  const placed = useMemo(() => (shapes ? centred(shapes) : null), [shapes]);
  const span = placed ? Math.max(...placed.size, 1) : 10;

  const sourceLabel = source === 'maker' ? "Maker's model" : source === 'drawn' ? 'Drawn from sourced sizes' : '';

  return (
    <div className="mf-library-preview" data-testid="library-preview" data-source={source}>
      <div className="mf-library-preview-stage">
        {placed && placed.shapes.length > 0 && (
          <Canvas
            key={part.name}
            camera={{ position: [span * 1.1, span * 0.9, span * 1.3], fov: 35, near: span / 200, far: span * 50 }}
            // The maker's stated colours are sRGB; the shared colour management leaves them as stated.
            gl={{ antialias: true, ...VIEWER_GPU_PREFERENCE, ...VIEWER_COLOR_MANAGEMENT }}
          >
            <hemisphereLight args={[0xffffff, 0x8890a0, 1.0]} />
            <ambientLight intensity={0.25} />
            <directionalLight position={[3, 5, 4]} intensity={0.8} />
            <directionalLight position={[-4, 2, -3]} intensity={0.3} />
            <group position={[0, -placed.size[1] / 2, 0]}>
              {placed.shapes.map((s, i) => (
                <ShapeMesh key={`${s.name}-${i}`} shape={s} />
              ))}
            </group>
            <OrbitControls makeDefault enableDamping />
          </Canvas>
        )}
        {!placed && !error && source !== 'none' && <p className="mf-library-preview-status">Loading model…</p>}
        {error && <p className="mf-library-preview-status mf-library-preview-error">Model not shown: {error}</p>}
        {source === 'none' && <p className="mf-library-preview-status">No model yet.</p>}
      </div>
      {placed && placed.shapes.length > 0 && (
        <p className="mf-library-preview-tag">
          {sourceLabel} ·{' '}
          <span className="font-mono">
            {placed.size[0].toFixed(1)} x {placed.size[2].toFixed(1)} x {placed.size[1].toFixed(1)} mm
          </span>
        </p>
      )}
    </div>
  );
}
