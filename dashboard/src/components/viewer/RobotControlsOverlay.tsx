import { useViewerStore } from '../../store/viewer-store';

const KC_SURFACE = 'rgba(30,31,38,0.92)';
const KC_BORDER = 'rgba(65,72,90,0.3)';
const KC_ON_SURFACE = '#e2e2eb';
const KC_ON_SURFACE_VARIANT = '#9a9aaa';

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

  return (
    <div
      className="absolute left-3 top-16 z-30 rounded flex flex-col gap-2 px-3 py-2"
      style={{ background: KC_SURFACE, backdropFilter: 'blur(16px)', border: `1px solid ${KC_BORDER}`, width: 260 }}
    >
      {robotError && (
        <div className="font-mono" style={{ fontSize: 10, color: '#ffb4ab' }}>
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
            </div>
          )}
        </>
      )}
    </div>
  );
}
