import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/** Cross-domain: nodes and links light up as dependent work is reached. */
const NODES: [number, number][] = [
  [30, 50],
  [80, 25],
  [80, 75],
  [130, 50],
];
const LINKS = ['M30 50 L80 25', 'M30 50 L80 75', 'M80 25 L130 50', 'M80 75 L130 50'];

export function CrossDomainScene(props: SceneProps) {
  return (
    <SceneFrame name="cross-domain" props={props}>
      {LINKS.map((d, i) => (
        <path
          key={d}
          className={cls('act-trace', i < props.stageIndex)}
          d={d}
          pathLength={100}
          style={{ animationDelay: `${i * 0.4}s` }}
        />
      ))}
      {NODES.map(([x, y], i) => (
        <circle
          key={`${x}-${y}`}
          className={cls('act-node', i < props.stageIndex)}
          cx={x}
          cy={y}
          r="7"
          style={{ animationDelay: `${i * 0.4}s` }}
        />
      ))}
    </SceneFrame>
  );
}
