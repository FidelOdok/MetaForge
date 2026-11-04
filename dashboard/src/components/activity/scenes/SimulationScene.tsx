import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/**
 * Simulation: a mesh assembles, then contour bands appear. The contours are
 * generic motion, not field data: no values are drawn.
 */
const MESH = 'M30 70 L80 25 L130 70 Z M55 47.5 L105 47.5 M55 47.5 L80 70 L105 47.5';
const CONTOURS = ['M40 66 Q80 40 120 66', 'M52 60 Q80 42 108 60', 'M64 54 Q80 44 96 54'];

export function SimulationScene(props: SceneProps) {
  return (
    <SceneFrame name="simulation" props={props}>
      <path className={cls('act-trace', props.stageIndex > 0)} d={MESH} pathLength={100} />
      {CONTOURS.map((d, i) => (
        <path
          key={d}
          className={cls('act-contour', props.stageIndex > 1)}
          d={d}
          style={{ animationDelay: `${i * 0.5}s` }}
        />
      ))}
    </SceneFrame>
  );
}
