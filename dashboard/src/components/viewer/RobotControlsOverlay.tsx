import { useViewerStore } from '../../store/viewer-store';
import { useComputeJointLoads } from '../../hooks/use-robot-loads';
import type { JointLoadResult } from '../../api/endpoints/robotLoads';

const KC_SURFACE = 'var(--mf-r-30-31-38-0p92)';
const KC_BORDER = 'var(--mf-r-65-72-90-0p3)';
const KC_ON_SURFACE = 'var(--mf-c-e2e2eb)';
const KC_ON_SURFACE_VARIANT = 'var(--mf-c-9a9aaa)';

function formatVec(v: [number, number, number]): string {
  return `[${v.map((n) => n.toFixed(1)).join(', ')}]`;
}

/**
 * MET-747: the HTML-overlay half of the "View Robot" consolidation —
 * physics toggle + joint sliders, positioned over the main viewer's Canvas
 * the same way GizmoControls/BooleanCutPanel already are. Reads/writes the
 * viewer store's robot slice rather than the live Three.js robot object
 * (RobotSceneContents, inside the Canvas, owns that) since an HTML sibling
 * of a react-three-fiber Canvas can't reach into it directly.
 */
export function RobotControlsOverlay() {
  const robotJoints = useViewerStore((s) => s.robotJoints);
  const robotJointValues = useViewerStore((s) => s.robotJointValues);
  const robotPhysicsEnabled = useViewerStore((s) => s.robotPhysicsEnabled);
  const robotError = useViewerStore((s) => s.robotError);
  const setRobotJointValue = useViewerStore((s) => s.setRobotJointValue);
  const setRobotPhysicsEnabled = useViewerStore((s) => s.setRobotPhysicsEnabled);
  const computeJointLoadChain = useViewerStore((s) => s.computeJointLoadChain);

  const jointLoadsMutation = useComputeJointLoads();

  const handleUseAsLoadCase = () => {
    if (!robotJoints || robotJoints.length === 0) return;
    const payload = computeJointLoadChain(robotJoints.map((j) => j.name));
    if (!payload || payload.links.length === 0) return;
    jointLoadsMutation.mutate(payload);
  };

  const worst: JointLoadResult | undefined = jointLoadsMutation.data?.worstJoint;

  return (
    <div
      className="absolute left-3 top-16 z-30 rounded flex flex-col gap-2 px-3 py-2"
      style={{ background: KC_SURFACE, backdropFilter: 'blur(16px)', border: `1px solid ${KC_BORDER}`, width: 260 }}
    >
      {robotError && (
        <div className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-ffb4ab)' }}>
          {robotError}
        </div>
      )}

      {!robotError && (
        <>
          <label className="font-mono flex items-center gap-1.5" style={{ fontSize: 10, color: KC_ON_SURFACE }}>
            <input
              type="checkbox"
              data-testid="main-viewer-robot-physics-toggle"
              checked={robotPhysicsEnabled}
              onChange={(e) => setRobotPhysicsEnabled(e.target.checked)}
            />
            Simulate with lightweight physics (gravity + joint constraints, cuboid colliders)
          </label>

          {!robotPhysicsEnabled && robotJoints && (
            <div className="flex flex-col gap-1.5">
              <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.08em', color: KC_ON_SURFACE_VARIANT }}>
                Joints ({robotJoints.length})
              </span>
              {robotJoints.length === 0 && (
                <span className="font-mono" style={{ fontSize: 10, color: KC_ON_SURFACE_VARIANT }}>
                  No movable joints in this assembly.
                </span>
              )}
              {robotJoints.map(({ name, lower, upper }) => (
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
                    value={robotJointValues[name] ?? 0}
                    onChange={(e) => setRobotJointValue(name, Number(e.target.value))}
                    style={{ flex: 1 }}
                  />
                </div>
              ))}
              {robotJoints.length > 0 && (
                <>
                  <button
                    type="button"
                    className="font-mono"
                    style={{
                      fontSize: 10,
                      color: KC_ON_SURFACE,
                      background: 'transparent',
                      border: `1px solid ${KC_BORDER}`,
                      borderRadius: 4,
                      padding: '4px 8px',
                      cursor: jointLoadsMutation.isPending ? 'default' : 'pointer',
                    }}
                    onClick={handleUseAsLoadCase}
                    disabled={jointLoadsMutation.isPending}
                  >
                    {jointLoadsMutation.isPending ? 'Computing…' : 'Use as load case'}
                  </button>
                  {jointLoadsMutation.isError && (
                    <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-ffb4ab)' }}>
                      Failed to compute joint loads.
                    </span>
                  )}
                  {worst && (
                    <div
                      className="font-mono flex flex-col gap-0.5"
                      style={{ fontSize: 10, color: KC_ON_SURFACE_VARIANT }}
                    >
                      <span>
                        Worst joint: <span style={{ color: KC_ON_SURFACE }}>{worst.jointName}</span> (
                        {worst.supportedMassKg.toFixed(2)} kg supported)
                      </span>
                      <span>Reaction force (N): {formatVec(worst.reactionForceN)}</span>
                      <span>Reaction moment (N·mm): {formatVec(worst.reactionMomentNMm)}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
