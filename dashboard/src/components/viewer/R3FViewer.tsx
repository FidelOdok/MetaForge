import { Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Environment, ContactShadows, Grid } from '@react-three/drei';
import { useViewerStore } from '../../store/viewer-store';
import { useThemeStore } from '../../store/theme-store';
import { SceneContents } from './SceneContents';
import type { PartInfo } from '../../types/viewer';

interface R3FViewerProps {
  onPartClick?: (part: PartInfo) => void;
}

function LoadingFallback() {
  return (
    <mesh>
      <boxGeometry args={[1, 1, 1]} />
      <meshStandardMaterial color="#888" wireframe />
    </mesh>
  );
}

export function R3FViewer({ onPartClick }: R3FViewerProps) {
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const manifest = useViewerStore((s) => s.manifest);
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
    <Canvas
      camera={{ position: [80, 60, 80], fov: 45, near: 0.1, far: 10000 }}
      style={{ background: bgColor }}
    >
      <Suspense fallback={<LoadingFallback />}>
        <SceneContents
          glbUrl={glbUrl}
          manifest={manifest}
          onPartClick={onPartClick}
        />
      </Suspense>

      <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
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
  );
}
