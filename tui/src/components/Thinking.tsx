import { useEffect, useState } from "react";
import { Text } from "ink";

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/**
 * In-progress indicator shown while the agent works: a braille spinner plus
 * elapsed seconds. Mounts when a turn starts and unmounts when it resolves, so
 * the elapsed clock is simply time-since-mount. ``label`` lets the caller show
 * "thinking" vs "reconnecting".
 */
// MET-641: one shared tick drives both the spinner frame and the elapsed
// clock, instead of two independent `setInterval`s at different phases —
// each was an unsynchronized trigger for a full Ink live-region repaint, so
// during a long tool-heavy turn (already repainting on every buffered
// delta/step flush) they doubled the effective repaint rate for no visible
// benefit. 150ms keeps the spin looking smooth while halving that overhead.
const TICK_MS = 150;

export function Thinking({ label = "thinking" }: { label?: string }) {
  const [frame, setFrame] = useState(0);
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const tick = setInterval(() => {
      setFrame((f) => (f + 1) % FRAMES.length);
      setSecs(Math.round((Date.now() - start) / 1000));
    }, TICK_MS);
    return () => clearInterval(tick);
  }, []);

  return (
    <Text color="yellow">
      {FRAMES[frame]} {label}…{secs > 0 ? ` ${secs}s` : ""}
    </Text>
  );
}
