import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/** Concept and sketch: strokes trace a product silhouette; reached strokes settle solid. */
const STROKES = [
  'M30 70 C30 40 50 28 80 28 C110 28 130 40 130 70',
  'M30 70 L130 70',
  'M48 70 L48 80 M112 70 L112 80',
  'M62 44 C70 38 90 38 98 44',
];

export function SketchScene(props: SceneProps) {
  return (
    <SceneFrame name="sketch" props={props}>
      {STROKES.map((d, i) => (
        <path
          key={d}
          className={cls('act-trace', i < props.stageIndex)}
          d={d}
          pathLength={100}
          style={{ animationDelay: `${i * 0.35}s` }}
        />
      ))}
    </SceneFrame>
  );
}
