import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/** Manufacturing: print layers build up, then inspection targets appear. No machine motion is implied. */
export function ManufacturingScene(props: SceneProps) {
  return (
    <SceneFrame name="manufacturing" props={props}>
      {[0, 1, 2, 3, 4].map((i) => (
        <path
          key={i}
          className={cls('act-trace', i < props.stageIndex)}
          d={`M${40 + i * 2} ${76 - i * 9} L${120 - i * 2} ${76 - i * 9}`}
          pathLength={100}
          style={{ animationDelay: `${i * 0.3}s` }}
        />
      ))}
      <circle className={cls('act-target', props.stageIndex > 4)} cx="50" cy="26" r="5" />
      <circle className={cls('act-target', props.stageIndex > 4)} cx="110" cy="26" r="5" />
    </SceneFrame>
  );
}
