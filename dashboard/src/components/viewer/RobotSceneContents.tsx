import { useEffect, useRef, useState } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Box3, Vector3 } from 'three';
import type { Mesh } from 'three';
import { useUrdfRobot } from '../../hooks/use-urdf-robot';
import { useUrdfPhysics } from '../../hooks/use-urdf-physics';
import { fetchNodeFileText, nodeMeshBaseUrl } from '../../api/endpoints/twin';
import { useViewerStore } from '../../store/viewer-store';
import { buildJointLoadChainPayload } from '../../lib/robot-statics';
import { RobotPoseDragControls } from '../../lib/robot-drag-controls';

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
  const registerJointLoadChainCompute = useViewerStore((s) => s.registerJointLoadChainCompute);

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

  // MET-747 fix: unlike SceneContents (the GLB path), this never published
  // modelBounds -- CameraController fell back to DEFAULT_BOUNDS (radius
  // 36mm, tuned for a small CAD part), leaving the camera framed far too
  // tight for a ~200-300mm robot. Live-verified: without this, the camera
  // sits inside/right next to one link, reading as "wrong geometry" when
  // it was actually just the wrong shot.
  //
  // Recomputed for ~90 frames (not once on [robot]) because each link's
  // <mesh> STL loads asynchronously inside urdf-loader (a background fetch
  // per link) -- `robot` itself is available the instant URDFLoader.parse()
  // returns, well before any of that geometry has actually attached, so a
  // single [robot]-triggered measurement caught an almost-empty box and
  // froze the camera at a near-zero-size framing (confirmed live: the whole
  // robot rendered as a barely-visible speck). Recomputing every frame for
  // ~1.5s lets the box grow to its real size as meshes finish loading, then
  // stops -- cheap (a handful of frames, one small object graph) and
  // self-correcting regardless of how long any individual STL fetch takes.
  const setModelBounds = useViewerStore((s) => s.setModelBounds);
  const boundsFrameCount = useRef(0);
  useEffect(() => {
    boundsFrameCount.current = 0;
  }, [robot]);
  useFrame(() => {
    if (!robot || boundsFrameCount.current >= 90) return;
    boundsFrameCount.current += 1;
    robot.updateMatrixWorld(true);
    const box = new Box3().setFromObject(robot);
    if (box.isEmpty()) return;
    const center = new Vector3();
    box.getCenter(center);
    const size = new Vector3();
    box.getSize(size);
    setModelBounds({
      center: [center.x, center.y, center.z],
      radius: Math.max(size.length() / 2, 1),
      groundY: box.min.y,
    });
  });

  // MET-747: same shadow flags SceneContents sets for GLB meshes -- without
  // this the viewer's shadow-casting light has nothing to shadow here either.
  useEffect(() => {
    if (!robot) return;
    robot.traverse((child) => {
      const mesh = child as Mesh;
      if (mesh.isMesh) {
        mesh.castShadow = true;
        mesh.receiveShadow = true;
      }
    });
  }, [robot]);

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

  // FORGE-283: registers a closure over the live `robot` object so
  // RobotControlsOverlay's "Use as load case" button (an HTML sibling that
  // can't reach into this Canvas child's Three.js objects) can read the
  // CURRENT posed transforms on click -- see viewer-store's
  // `_computeJointLoadChainFn` docstring for why this isn't just stored as
  // plain data instead.
  useEffect(() => {
    if (!robot) {
      registerJointLoadChainCompute(null);
      return;
    }
    registerJointLoadChainCompute((jointNames) => buildJointLoadChainPayload(robot, jointNames));
    return () => registerJointLoadChainCompute(null);
  }, [robot, registerJointLoadChainCompute]);

  useUrdfPhysics(robot, robotPhysicsEnabled);

  // Kinematic posing (physics mode drives link transforms itself instead).
  useEffect(() => {
    if (!robot || robotPhysicsEnabled) return;
    for (const [name, value] of Object.entries(robotJointValues)) {
      robot.setJointValue(name, value);
    }
  }, [robot, robotPhysicsEnabled, robotJointValues]);

  // FORGE-250: pointer-drag-a-link-to-pose-its-joint. Built (not just
  // disabled) only while kinematic control is active -- physics mode drives
  // joint transforms itself (useUrdfPhysics above), so a drag controller
  // fighting it over the same robot object would be meaningless as well as
  // actively wrong.
  const { camera, gl } = useThree();
  const setRobotJointValue = useViewerStore((s) => s.setRobotJointValue);
  const setRobotHoveredJoint = useViewerStore((s) => s.setRobotHoveredJoint);
  const setRobotDragging = useViewerStore((s) => s.setRobotDragging);
  const setOrbitControlsEnabled = useViewerStore((s) => s.setOrbitControlsEnabled);
  useEffect(() => {
    if (!robot || robotPhysicsEnabled) return;
    const controls = new RobotPoseDragControls(robot, camera, gl.domElement, {
      onJointChange: setRobotJointValue,
      onHoverChange: setRobotHoveredJoint,
      onDragStateChange: (dragging) => {
        setRobotDragging(dragging);
        setOrbitControlsEnabled(!dragging);
      },
    });
    return () => {
      controls.dispose();
      setRobotHoveredJoint(null);
      setRobotDragging(false);
      setOrbitControlsEnabled(true);
    };
  }, [
    robot,
    robotPhysicsEnabled,
    camera,
    gl,
    setRobotJointValue,
    setRobotHoveredJoint,
    setRobotDragging,
    setOrbitControlsEnabled,
  ]);

  if (!robot) return null;
  return <primitive object={robot} />;
}
