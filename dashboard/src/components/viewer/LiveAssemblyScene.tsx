/**
 * The 3D scene of an assembly being put together (FORGE-647).
 *
 * Each placed part is a committed twin part, so its model is already
 * converted and cached by the gateway: the scene loads that GLB and puts it
 * at the part's position and rotation. Nothing is converted per step, which
 * is why a part can appear about as fast as its event arrives.
 */
import { Suspense, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, useGLTF } from '@react-three/drei';
import { useQuery } from '@tanstack/react-query';
import * as THREE from 'three';

import { getNodeModel } from '../../api/endpoints/twin';
import { resolveGatewayHref } from '../../lib/gatewayConfig';
import type { LivePart } from '../../hooks/use-live-assembly';
import { VIEWER_COLOR_MANAGEMENT, VIEWER_GPU_PREFERENCE } from './viewerColor';

const DEG = Math.PI / 180;

function PartModel({ url, part }: { url: string; part: LivePart }) {
  const { scene } = useGLTF(url);
  // The same part can be placed twice; each placement needs its own instance.
  const instance = useMemo(() => scene.clone(true), [scene]);
  return (
    <primitive
      object={instance}
      position={part.position}
      rotation={new THREE.Euler(
        part.rotation[0] * DEG,
        part.rotation[1] * DEG,
        part.rotation[2] * DEG,
      )}
    />
  );
}

function LivePartMesh({ part }: { part: LivePart }) {
  const model = useQuery({
    queryKey: ['live-part-model', part.nodeId],
    queryFn: () => getNodeModel(part.nodeId),
    staleTime: 5 * 60_000,
  });
  if (!model.data) return null;
  return (
    <Suspense fallback={null}>
      <PartModel url={resolveGatewayHref(model.data.glb_url)} part={part} />
    </Suspense>
  );
}

export function LiveAssemblyScene({ parts }: { parts: LivePart[] }) {
  return (
    <Canvas
      gl={{ ...VIEWER_GPU_PREFERENCE, ...VIEWER_COLOR_MANAGEMENT }}
      camera={{ position: [260, 220, 260], near: 1, far: 20_000 }}
      data-testid="live-assembly-canvas"
    >
      <ambientLight intensity={0.7} />
      <directionalLight position={[200, 400, 300]} intensity={1.1} />
      <gridHelper args={[400, 20]} />
      {parts.map((part) => (
        <LivePartMesh key={part.nodeId} part={part} />
      ))}
      <OrbitControls makeDefault />
    </Canvas>
  );
}

export default LiveAssemblyScene;
