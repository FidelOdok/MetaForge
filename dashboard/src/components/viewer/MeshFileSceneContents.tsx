import { SceneBody, type SceneBodyProps } from './SceneContents';
import { useMeshFileObject } from './meshFileObject';

/** FORGE-531: an STL/3MF file in the main viewer, with full selection/explode behaviour. */
interface MeshFileSceneContentsProps extends Omit<SceneBodyProps, 'scene'> {
  glbUrl: string;
  format: string;
}

export function MeshFileSceneContents({ glbUrl, format, ...props }: MeshFileSceneContentsProps) {
  const scene = useMeshFileObject(glbUrl, format);
  return <SceneBody scene={scene} {...props} />;
}
