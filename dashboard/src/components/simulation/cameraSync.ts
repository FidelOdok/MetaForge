/** A tiny pub/sub that keeps several field viewers' cameras in step, for
 * the side-by-side compare (FORGE-532). Each viewer publishes its own
 * camera pose with its `source` id and applies every other viewer's. */
export interface CameraPose {
  position: [number, number, number];
  target: [number, number, number];
}

type Listener = (pose: CameraPose, source: string) => void;

export interface CameraSync {
  publish(pose: CameraPose, source: string): void;
  subscribe(listener: Listener): () => void;
}

export function createCameraSync(): CameraSync {
  const listeners = new Set<Listener>();
  return {
    publish(pose, source) {
      listeners.forEach((listener) => listener(pose, source));
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
