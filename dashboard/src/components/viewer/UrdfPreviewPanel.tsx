import { Suspense, useEffect, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import type { URDFJoint } from 'urdf-loader';
import { useUrdfRobot, meshBaseUrlFrom } from '../../hooks/use-urdf-robot';
import { useUrdfPhysics } from '../../hooks/use-urdf-physics';
import { toDownloadHref, type ExportFile } from '../../api/endpoints/cad-export';

const KC_SURFACE = 'rgba(30,31,38,0.92)';
const KC_BORDER = 'rgba(65,72,90,0.3)';
const KC_BORDER_MID = 'rgba(65,72,90,0.45)';
const KC_ON_SURFACE = '#e2e2eb';
const KC_ON_SURFACE_VARIANT = '#9a9aaa';

interface UrdfPreviewPanelProps {
  /** The just-exported URDF file (any format's assembly export result can
   * feed this — see the module docstring on why SDF/USD reuse it too). */
  urdfFile: ExportFile;
  onClose: () => void;
}

/**
 * MET-737: render the exported URDF as an articulated 3D model, with either
 * kinematic joint sliders or (opt-in) a lightweight best-effort physics
 * simulation — see `use-urdf-physics.ts`'s docstring for what that does and
 * doesn't cover.
 *
 * Two boundaries surfaced in the UI itself, not just code comments:
 * - This is always a URDF-sourced preview, even when the user is about to
 *   download SDF or USD. No browser-ready SDF parser exists, and real USD
 *   rendering in-browser is unsolved (heavy, immature WASM builds of the
 *   OpenUSD stack exist but aren't used here). The preview approximates the
 *   SDF/USD export (same parts+joints) rather than literally rendering that
 *   file — labeled "Assembly preview", not "USD preview" / "SDF preview".
 * - Physics mode is a lightweight/best-effort simulation (cuboid colliders,
 *   no contact between links, no re-sync with slider posing) — not a
 *   substitute for a real robotics simulator (Gazebo/Isaac Sim), which is
 *   exactly why this pipeline also exports real SDF/USD for those tools.
 */
export function UrdfPreviewPanel({ urdfFile, onClose }: UrdfPreviewPanelProps) {
  const [urdfText, setUrdfText] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [physicsEnabled, setPhysicsEnabled] = useState(false);
  const [jointValues, setJointValues] = useState<Record<string, number>>({});

  const href = toDownloadHref(urdfFile.download_url);
  const meshBaseUrl = meshBaseUrlFrom(href);

  useEffect(() => {
    let cancelled = false;
    setUrdfText(null);
    setFetchError(null);
    fetch(href)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.text();
      })
      .then((text) => {
        if (!cancelled) setUrdfText(text);
      })
      .catch((err) => {
        if (!cancelled) setFetchError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [href]);

  const { robot, error: parseError } = useUrdfRobot(urdfText, meshBaseUrl);

  useEffect(() => {
    if (!robot) return;
    const initial: Record<string, number> = {};
    for (const [name, joint] of Object.entries(robot.joints)) {
      initial[name] = Array.isArray(joint.jointValue) ? (joint.jointValue[0] ?? 0) : 0;
    }
    setJointValues(initial);
  }, [robot]);

  const nonFixedJoints: [string, URDFJoint][] = robot
    ? Object.entries(robot.joints).filter(([, j]) => j.jointType !== 'fixed')
    : [];

  return (
    <div
      className="rounded flex flex-col overflow-hidden"
      style={{ background: KC_SURFACE, backdropFilter: 'blur(16px)', border: `1px solid ${KC_BORDER_MID}`, width: 420, maxHeight: 'calc(100vh - 88px)' }}
    >
      <div className="flex items-center justify-between px-3 flex-shrink-0" style={{ height: 36, borderBottom: `1px solid ${KC_BORDER}` }}>
        <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC_ON_SURFACE_VARIANT }}>
          Assembly preview
        </span>
        <button
          type="button"
          onClick={onClose}
          data-testid="urdf-preview-close"
          style={{ background: 'transparent', border: 'none', color: KC_ON_SURFACE_VARIANT, cursor: 'pointer', padding: 2 }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="font-mono px-3 pt-2" style={{ fontSize: 9, color: KC_ON_SURFACE_VARIANT, lineHeight: 1.4 }}>
        Approximates the export (built from the same parts + joints) — it does not
        literally render the SDF/USD file, and posing here is kinematic/best-effort
        physics only, not a substitute for Gazebo/Isaac Sim.
      </div>

      <div style={{ height: 260, position: 'relative', margin: '8px 12px', borderRadius: 4, overflow: 'hidden', border: `1px solid ${KC_BORDER}` }}>
        {fetchError && (
          <div className="font-mono absolute inset-0 flex items-center justify-center p-2 text-center" style={{ fontSize: 10, color: '#ffb4ab' }}>
            Failed to load URDF: {fetchError}
          </div>
        )}
        {parseError && (
          <div className="font-mono absolute inset-0 flex items-center justify-center p-2 text-center" style={{ fontSize: 10, color: '#ffb4ab' }}>
            Failed to parse URDF: {parseError.message}
          </div>
        )}
        {!fetchError && !parseError && (
          <Canvas
            data-testid="urdf-preview-canvas"
            camera={{ position: [1, 0.8, 1], fov: 50, near: 0.001, far: 1000 }}
            gl={{ preserveDrawingBuffer: true }}
            style={{ background: '#1e1f26' }}
          >
            <Suspense fallback={null}>
              {robot && (
                <RobotScene
                  robot={robot}
                  physicsEnabled={physicsEnabled}
                  jointValues={jointValues}
                />
              )}
            </Suspense>
            <ambientLight intensity={0.6} />
            <directionalLight position={[2, 3, 2]} intensity={0.9} />
            <OrbitControls makeDefault />
          </Canvas>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-3" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <label className="font-mono flex items-center gap-1.5" style={{ fontSize: 10, color: KC_ON_SURFACE }}>
          <input
            type="checkbox"
            data-testid="urdf-preview-physics-toggle"
            checked={physicsEnabled}
            onChange={(e) => setPhysicsEnabled(e.target.checked)}
          />
          Simulate with lightweight physics (gravity + joint constraints, cuboid colliders)
        </label>

        {!physicsEnabled && (
          <div className="flex flex-col gap-1.5">
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.08em', color: KC_ON_SURFACE_VARIANT }}>
              Joints ({nonFixedJoints.length})
            </span>
            {nonFixedJoints.length === 0 && robot && (
              <span className="font-mono" style={{ fontSize: 10, color: KC_ON_SURFACE_VARIANT }}>
                No movable joints in this assembly.
              </span>
            )}
            {nonFixedJoints.map(([name, joint]) => {
              const isContinuous = joint.jointType === 'continuous';
              const lower = isContinuous ? -Math.PI : joint.limit.lower;
              const upper = isContinuous ? Math.PI : joint.limit.upper;
              return (
                <div key={name} className="flex items-center gap-1.5">
                  <span className="font-mono" style={{ fontSize: 10, color: KC_ON_SURFACE, width: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>
                    {name}
                  </span>
                  <input
                    type="range"
                    aria-label={`joint ${name}`}
                    min={lower}
                    max={upper}
                    step={(upper - lower) / 200 || 0.01}
                    value={jointValues[name] ?? 0}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      setJointValues((prev) => ({ ...prev, [name]: v }));
                      robot?.setJointValue(name, v);
                    }}
                    style={{ flex: 1 }}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function RobotScene({
  robot,
  physicsEnabled,
  jointValues,
}: {
  robot: import('urdf-loader').URDFRobot;
  physicsEnabled: boolean;
  jointValues: Record<string, number>;
}) {
  useUrdfPhysics(robot, physicsEnabled);

  // Re-apply slider values whenever they change (kinematic mode only —
  // physics mode drives link transforms itself via use-urdf-physics).
  useEffect(() => {
    if (physicsEnabled) return;
    for (const [name, value] of Object.entries(jointValues)) {
      robot.setJointValue(name, value);
    }
  }, [robot, physicsEnabled, jointValues]);

  return <primitive object={robot} />;
}
