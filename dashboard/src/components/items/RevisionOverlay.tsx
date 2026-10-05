import { Suspense, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei';
import { useQuery } from '@tanstack/react-query';
import * as THREE from 'three';
import { getNodeModel } from '../../api/endpoints/twin';
import { resolveGatewayHref } from '../../lib/gatewayConfig';

/* FORGE-526: two revisions of one part in one small canvas. The newer
 * revision renders as committed; the older one is a translucent ghost, so a
 * changed outline reads at a glance. Own Canvas, like StructureView's
 * GlbPreview: the main viewer's store stays on whatever the page loaded. */

const GHOST = new THREE.MeshStandardMaterial({
  color: '#86cfff',
  transparent: true,
  opacity: 0.22,
  depthWrite: false,
});

function Model({ url, ghost }: { url: string; ghost: boolean }) {
  const { scene } = useGLTF(url);
  const object = useMemo(() => {
    const copy = scene.clone(true);
    if (ghost) {
      copy.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) (child as THREE.Mesh).material = GHOST;
      });
    }
    return copy;
  }, [scene, ghost]);
  return <primitive object={object} />;
}

function useGlbUrl(nodeId: string) {
  return useQuery({
    queryKey: ['items', 'overlay-model', nodeId],
    queryFn: async () => resolveGatewayHref((await getNodeModel(nodeId)).glb_url),
    staleTime: 300_000,
    retry: false,
  });
}

export function RevisionOverlay({
  oldNodeId,
  newNodeId,
  oldLabel,
  newLabel,
}: {
  oldNodeId: string;
  newNodeId: string;
  oldLabel: string;
  newLabel: string;
}) {
  const oldUrl = useGlbUrl(oldNodeId);
  const newUrl = useGlbUrl(newNodeId);
  const failed = oldUrl.isError || newUrl.isError;

  return (
    <div data-testid="revision-overlay">
      <div className="flex items-center gap-3 mb-1 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
        <span className="flex items-center gap-1">
          <span style={{ width: 8, height: 8, borderRadius: 2, background: 'rgba(134,207,255,0.35)', display: 'inline-block' }} />
          {oldLabel} (ghost)
        </span>
        <span className="flex items-center gap-1">
          <span style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--mf-c-e2e2eb)', display: 'inline-block' }} />
          {newLabel}
        </span>
      </div>
      {failed ? (
        <div className="font-mono rounded p-3" style={{ fontSize: 11, color: 'var(--mf-c-9a9aaa)', background: 'var(--mf-c-191b22)' }}>
          Geometry preview unavailable for one of these revisions. The numbers below still compare them.
        </div>
      ) : !oldUrl.data || !newUrl.data ? (
        <div className="font-mono rounded p-3 animate-pulse" style={{ fontSize: 11, color: 'var(--mf-c-9a9aaa)', background: 'var(--mf-c-191b22)' }}>
          Loading both revisions...
        </div>
      ) : (
        <Canvas camera={{ position: [2, 2, 2], fov: 45 }} style={{ height: 240, background: 'var(--mf-c-191b22)', borderRadius: 6 }}>
          <ambientLight intensity={0.7} />
          <directionalLight position={[5, 5, 5]} intensity={0.9} />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.2}>
              <Model url={newUrl.data} ghost={false} />
              <Model url={oldUrl.data} ghost />
            </Bounds>
          </Suspense>
          <OrbitControls makeDefault />
        </Canvas>
      )}
    </div>
  );
}
