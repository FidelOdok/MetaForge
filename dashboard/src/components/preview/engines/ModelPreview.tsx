import { Suspense, useEffect, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei';
import type * as THREE from 'three';
import { getNodeModel, nodeFileUrl } from '../../../api/endpoints/twin';
import { resolveGatewayHref } from '../../../lib/gatewayConfig';
import { VIEWER_COLOR_MANAGEMENT } from '../../viewer/viewerColor';
import { useMeshFileObject } from '../../viewer/meshFileObject';
import { DIRECT_MESH_FORMATS } from '../registry';
import { AssemblyPartTree } from '../../viewer/AssemblyPartTree';
import { ErrorBoundary } from '../../ErrorBoundary';
import { PC, type PreviewMode } from '../tokens';
import type { TwinNode } from '../../../types/twin';

/**
 * FORGE-531: a self-contained 3D preview for the full-screen modal and
 * project rows. `cad3d` goes through the STEP->GLB converter route
 * (GET /v1/twin/nodes/{id}/model), exactly like the main viewer; `mesh3d`
 * loads the STL/3MF/GLB file itself with three's loaders. Separate from the
 * main R3FViewer on purpose: that one is bound to the global viewer store
 * (selection, explode, gizmo), and a preview must not steal its model.
 */
interface ModelPreviewProps {
  node: TwinNode;
  engine: 'cad3d' | 'mesh3d';
  format: string;
  mode: PreviewMode;
}

function GltfObject({ url }: { url: string }) {
  const { scene } = useGLTF(url);
  // A cached glTF scene can only have one parent; clone so the main viewer
  // keeps its own copy.
  const copy = useMemo(() => scene.clone(true), [scene]);
  return <primitive object={copy} />;
}

function MeshFileObject({ url, format }: { url: string; format: string }) {
  const obj = useMeshFileObject(url, format);
  const copy = useMemo(() => (obj as THREE.Object3D).clone(true), [obj]);
  return <primitive object={copy} />;
}

function Message({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center font-mono text-center px-4" style={{ fontSize: 12, color: PC.onSurfaceVariant }}>
      {children}
    </div>
  );
}

export function ModelPreview({ node, engine, format, mode }: ModelPreviewProps) {
  const [url, setUrl] = useState<string | null>(engine === 'mesh3d' ? nodeFileUrl(node.id) : null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (engine === 'mesh3d') {
      setUrl(nodeFileUrl(node.id));
      return;
    }
    let cancelled = false;
    setUrl(null);
    setError(null);
    getNodeModel(node.id)
      .then((r) => {
        if (!cancelled) setUrl(resolveGatewayHref(r.glb_url));
      })
      .catch(() => {
        if (!cancelled) setError('This work product has no viewable 3D model yet.');
      });
    return () => {
      cancelled = true;
    };
  }, [node.id, engine]);

  const direct = engine === 'mesh3d' && DIRECT_MESH_FORMATS.has(format);
  const parts = node.assemblyParts ?? [];
  const showTree = mode === 'modal' && parts.length > 0;

  return (
    <div className="flex h-full w-full min-h-0" data-testid="preview-model">
      <div className="relative flex-1 min-w-0" style={{ background: 'var(--mf-c-0a0b0f)' }}>
        {error ? (
          <Message>{error}</Message>
        ) : !url ? (
          <Message>Converting model…</Message>
        ) : (
          <ErrorBoundary fallback={<Message>The 3D file could not be read.</Message>}>
            <Canvas
              camera={{ position: [80, 60, 80], fov: 45, near: 0.1, far: 100000 }}
              gl={{ ...VIEWER_COLOR_MANAGEMENT }}
              style={{ width: '100%', height: '100%' }}
            >
              <ambientLight intensity={0.6} />
              <directionalLight position={[100, 160, 120]} intensity={1.6} />
              <directionalLight position={[-120, -40, -80]} intensity={0.5} />
              <Suspense fallback={null}>
                <Bounds fit clip observe margin={1.2}>
                  {direct ? <MeshFileObject url={url} format={format} /> : <GltfObject url={url} />}
                </Bounds>
              </Suspense>
              <OrbitControls makeDefault />
            </Canvas>
          </ErrorBoundary>
        )}
      </div>
      {showTree && (
        <aside className="overflow-y-auto flex-shrink-0" style={{ width: 300, borderLeft: `1px solid ${PC.border}`, background: PC.surfaceLow, padding: 12 }}>
          <div className="font-mono uppercase mb-2" style={{ fontSize: 10, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>
            Assembly parts ({parts.length})
          </div>
          <AssemblyPartTree parts={parts} />
        </aside>
      )}
    </div>
  );
}
