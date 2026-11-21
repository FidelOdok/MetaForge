import { Suspense, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei';
import { useAuthedObjectUrl } from '../../shared/AuthedImg';
import { ErrorBoundary } from '../../ErrorBoundary';
import { VIEWER_COLOR_MANAGEMENT, VIEWER_GPU_PREFERENCE } from '../../viewer/viewerColor';
import { decideUpAxis, uprightRotationX, type GltfAssetInfo } from '../../../lib/up-axis';

function Board({ url }: { url: string }) {
  const { scene, asset } = useGLTF(url);
  const copy = useMemo(() => scene.clone(true), [scene]);
  const { axis } = decideUpAxis({ asset: asset as GltfAssetInfo | undefined, scene, format: 'glb', source: 'file' });
  return (
    <group rotation={[uprightRotationX(axis), 0, 0]}>
      <primitive object={copy} />
    </group>
  );
}

/**
 * One board in 3D, populated: the GLB kicad.export_3d made when the layout
 * was recorded. Loaded with the person's sign-in, then shown from a local
 * object URL (a model loader cannot send the token either).
 */
export function BoardModel3D({ src, label }: { src: string; label: string }) {
  const { url, error } = useAuthedObjectUrl(src);
  if (error) {
    return (
      <p className="cv-muted" role="status">
        The 3D model of {label} {error}.
      </p>
    );
  }
  if (!url) {
    return (
      <p className="cv-muted" role="status">
        Loading the 3D model of {label}…
      </p>
    );
  }
  return (
    <div className="cv-board-3d" data-testid="board-3d" aria-label={`${label} in 3D`}>
      <ErrorBoundary fallback={<p className="cv-muted">The 3D model of {label} could not be read.</p>}>
        <Canvas
          camera={{ position: [40, 40, 40], fov: 40, near: 0.1, far: 10000 }}
          gl={{ ...VIEWER_GPU_PREFERENCE, ...VIEWER_COLOR_MANAGEMENT }}
          style={{ width: '100%', height: '100%' }}
        >
          <ambientLight intensity={0.7} />
          <directionalLight position={[60, 120, 80]} intensity={1.6} />
          <directionalLight position={[-80, -30, -60]} intensity={0.5} />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.3}>
              <Board url={url} />
            </Bounds>
          </Suspense>
          <OrbitControls makeDefault />
        </Canvas>
      </ErrorBoundary>
    </div>
  );
}
