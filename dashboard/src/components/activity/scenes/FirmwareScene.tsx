import { SceneFrame } from './SceneFrame';
import { cls, type SceneProps } from './types';

/** Firmware: a controller receives signal packets; build stages tick through. */
export function FirmwareScene(props: SceneProps) {
  return (
    <SceneFrame name="firmware" props={props}>
      <rect className="act-chip" x="96" y="30" width="40" height="40" rx="3" />
      <path className="act-bus" d="M20 42 L96 42 M20 58 L96 58" />
      <rect className="act-packet" x="24" y="38" width="10" height="8" rx="1" />
      <rect className="act-packet" x="24" y="54" width="10" height="8" rx="1" style={{ animationDelay: '0.6s' }} />
      {[0, 1, 2, 3].map((i) => (
        <rect key={i} className={cls('act-tick', i < props.stageIndex)} x={40 + i * 14} y="80" width="10" height="6" rx="1" />
      ))}
    </SceneFrame>
  );
}
