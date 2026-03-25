import { Suspense, useEffect, useRef } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, Environment, ContactShadows, Grid } from '@react-three/drei';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { RotateCcw } from 'lucide-react';
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

export function R3FViewer({ onPartClick }: R3FViewerProps) {
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const manifest = useViewerStore((s) => s.manifest);
  const resetCamera = useViewerStore((s) => s.resetCamera);
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
    <div className="relative h-full w-full">
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

        <CameraController />
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

      {/* Controls hint overlay */}
      <div className="absolute bottom-3 left-3 z-10 rounded-md bg-black/50 px-3 py-1.5 text-xs text-white/80 select-none pointer-events-none">
        Left drag: rotate · Scroll: zoom · Right drag: pan
      </div>

      {/* Camera reset button */}
      <button
        type="button"
        onClick={resetCamera}
        className="absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-md bg-black/50 px-2.5 py-1.5 text-xs text-white/80 transition-colors hover:bg-black/70 hover:text-white"
        title="Reset camera to default view"
        aria-label="Reset camera"
      >
        <RotateCcw size={12} />
        Reset view
      </button>
    </div>
  );
}
