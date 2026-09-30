import { useEffect, useRef, useState } from 'react';
import { useViewerStore } from '../../store/viewer-store';
import { useComputeJointLoads } from '../../hooks/use-robot-loads';
import { useTwinNode, useSaveRobotPose } from '../../hooks/use-twin';
import type { JointLoadResult } from '../../api/endpoints/robotLoads';
import {
  clampToJointLimits,
  zeroPose,
  homePose,
  minPose,
  maxPose,
  runPoseAnimation,
  type PoseValues,
} from '../../lib/robot-poses';
import type { RobotJointInfo } from '../../store/viewer-store';

const KC_SURFACE = 'var(--mf-r-30-31-38-0p92)';
const KC_BORDER = 'var(--mf-r-65-72-90-0p3)';
const KC_ON_SURFACE = 'var(--mf-c-e2e2eb)';
const KC_ON_SURFACE_VARIANT = 'var(--mf-c-9a9aaa)';
const KC_ACCENT = '#ff5a0a';

function formatVec(v: [number, number, number]): string {
  return `[${v.map((n) => n.toFixed(1)).join(', ')}]`;
}

const buttonStyle = {
  fontSize: 10,
  color: KC_ON_SURFACE,
  background: 'transparent',
  border: `1px solid ${KC_BORDER}`,
  borderRadius: 4,
  padding: '3px 7px',
  cursor: 'pointer' as const,
};

/** FORGE-250: the four built-in poses, computed fresh from the current
 * joint list rather than stored anywhere -- they're a pure function of
 * each joint's [lower, upper] range, so there's nothing to persist. */
function builtinPresets(joints: RobotJointInfo[]): { label: string; values: PoseValues }[] {
  return [
    { label: 'Zero', values: zeroPose(joints) },
    { label: 'Home', values: homePose(joints) },
    { label: 'Min', values: minPose(joints) },
    { label: 'Max', values: maxPose(joints) },
  ];
}

/**
 * MET-747: the HTML-overlay half of the "View Robot" consolidation --
 * physics toggle + joint sliders, positioned over the main viewer's Canvas
 * the same way GizmoControls/BooleanCutPanel already are. Reads/writes the
 * viewer store's robot slice rather than the live Three.js robot object
 * (RobotSceneContents, inside the Canvas, owns that) since an HTML sibling
 * of a react-three-fiber Canvas can't reach into it directly.
 *
 * FORGE-250 adds pose presets (built-in + saved) with animated transitions,
 * on top of the drag-to-pose interaction that lives inside RobotSceneContents
 * (the Canvas child raycasting against the live robot object) -- this panel
 * only drives it via the same `robotJointValues` store slice sliders already
 * use, and shows the joint name RobotSceneContents' drag controller reports
 * as currently hovered.
 */
export function RobotControlsOverlay() {
  const nodeId = useViewerStore((s) => s.robotDescription?.nodeId);
  const robotJoints = useViewerStore((s) => s.robotJoints);
  const robotJointValues = useViewerStore((s) => s.robotJointValues);
  const robotPhysicsEnabled = useViewerStore((s) => s.robotPhysicsEnabled);
  const robotError = useViewerStore((s) => s.robotError);
  const robotHoveredJoint = useViewerStore((s) => s.robotHoveredJoint);
  const setRobotJointValue = useViewerStore((s) => s.setRobotJointValue);
  const setRobotPhysicsEnabled = useViewerStore((s) => s.setRobotPhysicsEnabled);
  const computeJointLoadChain = useViewerStore((s) => s.computeJointLoadChain);

  const jointLoadsMutation = useComputeJointLoads();
  const { data: node } = useTwinNode(nodeId);
  const savePoseMutation = useSaveRobotPose();
  const [poseName, setPoseName] = useState('');

  const cancelAnimationRef = useRef<(() => void) | null>(null);
  useEffect(() => () => cancelAnimationRef.current?.(), []);

  const animateToPose = (target: PoseValues) => {
    if (!robotJoints) return;
    cancelAnimationRef.current?.();
    const clamped = clampToJointLimits(robotJoints, target);
    cancelAnimationRef.current = runPoseAnimation(robotJointValues, clamped, (values) => {
      for (const [name, value] of Object.entries(values)) setRobotJointValue(name, value);
    });
  };

  const handleUseAsLoadCase = () => {
    if (!robotJoints || robotJoints.length === 0) return;
    const payload = computeJointLoadChain(robotJoints.map((j) => j.name));
    if (!payload || payload.links.length === 0) return;
    jointLoadsMutation.mutate(payload);
  };

  const handleSavePose = () => {
    if (!nodeId || !poseName.trim()) return;
    savePoseMutation.mutate({
      nodeId,
      existingPoses: node?.poses,
      poseName: poseName.trim(),
      values: robotJointValues,
    });
    setPoseName('');
  };

  const worst: JointLoadResult | undefined = jointLoadsMutation.data?.worstJoint;
  const savedPoses = node?.poses ?? {};

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

              {/* FORGE-250: drag-to-pose happens directly on the 3D links
                  (see RobotSceneContents); this line is the HTML-overlay
                  half of "hovering a link ... names its joint", since the
                  Canvas has no DOM text of its own to show it in. */}
              {robotHoveredJoint && (
                <span className="font-mono" style={{ fontSize: 10, color: KC_ACCENT }}>
                  Hovering: {robotHoveredJoint}
                </span>
              )}

              {robotJoints.length > 0 && (
                <>
                  <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.08em', color: KC_ON_SURFACE_VARIANT }}>
                    Poses
                  </span>
                  <div className="flex flex-wrap gap-1">
                    {builtinPresets(robotJoints).map(({ label, values }) => (
                      <button
                        key={label}
                        type="button"
                        className="font-mono"
                        style={buttonStyle}
                        onClick={() => animateToPose(values)}
                      >
                        {label}
                      </button>
                    ))}
                    {Object.entries(savedPoses).map(([name, values]) => (
                      <button
                        key={name}
                        type="button"
                        className="font-mono"
                        style={buttonStyle}
                        title={`Saved pose "${name}"`}
                        onClick={() => animateToPose(values)}
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      type="text"
                      aria-label="New pose name"
                      placeholder="Pose name…"
                      value={poseName}
                      onChange={(e) => setPoseName(e.target.value)}
                      className="font-mono"
                      style={{
                        flex: 1,
                        fontSize: 10,
                        color: KC_ON_SURFACE,
                        background: 'transparent',
                        border: `1px solid ${KC_BORDER}`,
                        borderRadius: 4,
                        padding: '3px 6px',
                      }}
                    />
                    <button
                      type="button"
                      className="font-mono"
                      style={{ ...buttonStyle, cursor: !nodeId || !poseName.trim() || savePoseMutation.isPending ? 'default' : 'pointer' }}
                      onClick={handleSavePose}
                      disabled={!nodeId || !poseName.trim() || savePoseMutation.isPending}
                    >
                      {savePoseMutation.isPending ? 'Saving…' : 'Save current pose'}
                    </button>
                  </div>
                  {savePoseMutation.isError && (
                    <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-ffb4ab)' }}>
                      Failed to save pose.
                    </span>
                  )}

                  <button
                    type="button"
                    className="font-mono"
                    style={{
                      ...buttonStyle,
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
