import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/** EDA: copper traces connect pads. Illustrative: it never implies routing or DRC passed. */
const PADS: [number, number][] = [
  [30, 30],
  [30, 70],
  [130, 30],
  [130, 70],
];
const TRACES = ['M30 30 L70 30 L90 50 L130 50 L130 70', 'M30 70 L60 70 L80 50', 'M130 30 L100 30 L90 40'];

export function EdaScene(props: SceneProps) {
  return (
    <SceneFrame name="eda" props={props}>
      <rect className="act-board" x="18" y="18" width="124" height="64" rx="4" />
      <rect className="act-chip" x="70" y="38" width="20" height="24" rx="2" />
      {TRACES.map((d, i) => (
        <path
          key={d}
          className={cls('act-trace', i < props.stageIndex)}
          d={d}
          pathLength={100}
          style={{ animationDelay: `${i * 0.5}s` }}
        />
      ))}
      {PADS.map(([x, y]) => (
        <circle key={`${x}-${y}`} className="act-pad" cx={x} cy={y} r="4" />
      ))}
    </SceneFrame>
  );
}
