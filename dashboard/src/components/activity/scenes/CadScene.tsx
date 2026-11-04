import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/** CAD: a profile extrudes into a wireframe solid. */
export function CadScene(props: SceneProps) {
  const reached = (i: number) => i < props.stageIndex;
  return (
    <SceneFrame name="cad" props={props}>
      <path className={cls('act-trace', reached(0))} d="M40 75 L100 75 L100 45 L40 45 Z" pathLength={100} />
      <g className="act-extrude">
        <path
          className={cls('act-trace', reached(1))}
          d="M60 60 L120 60 L120 30 L60 30 Z"
          pathLength={100}
          style={{ animationDelay: '0.4s' }}
        />
      </g>
      <path
        className={cls('act-trace', reached(2))}
        d="M40 75 L60 60 M100 75 L120 60 M100 45 L120 30 M40 45 L60 30"
        pathLength={100}
        style={{ animationDelay: '0.8s' }}
      />
    </SceneFrame>
  );
}
