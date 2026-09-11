import { useEffect, useRef, useState } from 'react';
import { useUrdfRobot } from '../../hooks/use-urdf-robot';
import { useUrdfPhysics } from '../../hooks/use-urdf-physics';
import { fetchNodeFileText, nodeMeshBaseUrl } from '../../api/endpoints/twin';
import { useViewerStore } from '../../store/viewer-store';

/**
 * MET-747: the Canvas-side half of the "View Robot" consolidation. Fetches
 * and parses one robot_description node's URDF, same as the old standalone
 * `UrdfPreviewPanel` popup did, but renders as a child of the MAIN viewer's
 * one Canvas (mounted by R3FViewer, lazy-loaded so an ordinary CAD_MODEL
 * viewer never pulls in urdf-loader/@dimforge/rapier3d-compat) instead of a
 * second, independent Canvas floating over it.
 *
 * Joint list + values + the physics toggle live in the viewer store, not
 * local state — RobotControlsOverlay (an HTML sibling of this Canvas
 * child, so it can't reach into this component's Three.js objects
 * directly) reads/writes the same store slice to render its sliders.
 */
export function RobotSceneContents({ nodeId }: { nodeId: string }) {
  const [urdfText, setUrdfText] = useState<string | null>(null);
  const setRobotError = useViewerStore((s) => s.setRobotError);
  const setRobotJoints = useViewerStore((s) => s.setRobotJoints);
  const robotJointValues = useViewerStore((s) => s.robotJointValues);
  const robotPhysicsEnabled = useViewerStore((s) => s.robotPhysicsEnabled);

  const meshBaseUrl = nodeMeshBaseUrl(nodeId);

  useEffect(() => {
    let cancelled = false;
    setUrdfText(null);
    fetchNodeFileText(nodeId)
      .then((text) => {
        if (!cancelled) setUrdfText(text);
      })
      .catch((err) => {
        if (!cancelled) {
          setRobotError(`Failed to load URDF: ${err instanceof Error ? err.message : String(err)}`);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId]);

  const { robot, error: parseError } = useUrdfRobot(urdfText, meshBaseUrl);

  useEffect(() => {
    if (parseError) setRobotError(`Failed to parse URDF: ${parseError.message}`);
  }, [parseError, setRobotError]);

  const jointsInitialized = useRef<string | null>(null);
  useEffect(() => {
    if (!robot || jointsInitialized.current === nodeId) return;
    jointsInitialized.current = nodeId;
    const joints = Object.entries(robot.joints)
      .filter(([, j]) => j.jointType !== 'fixed')
      .map(([name, joint]) => {
        const isContinuous = joint.jointType === 'continuous';
        return {
          name,
          lower: isContinuous ? -Math.PI : joint.limit.lower,
          upper: isContinuous ? Math.PI : joint.limit.upper,
          initial: Array.isArray(joint.jointValue) ? (joint.jointValue[0] ?? 0) : 0,
        };
      });
    setRobotJoints(joints);
  }, [robot, nodeId, setRobotJoints]);

  useUrdfPhysics(robot, robotPhysicsEnabled);

  // Kinematic posing (physics mode drives link transforms itself instead).
  useEffect(() => {
    if (!robot || robotPhysicsEnabled) return;
    for (const [name, value] of Object.entries(robotJointValues)) {
      robot.setJointValue(name, value);
    }
  }, [robot, robotPhysicsEnabled, robotJointValues]);

  if (!robot) return null;
  return <primitive object={robot} />;
}
