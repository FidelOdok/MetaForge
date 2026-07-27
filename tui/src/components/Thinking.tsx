import { useEffect, useState } from "react";
import { Text } from "ink";

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/**
 * In-progress indicator shown while the agent works: a braille spinner plus
 * elapsed seconds. Mounts when a turn starts and unmounts when it resolves, so
 * the elapsed clock is simply time-since-mount. ``label`` lets the caller show
 * "thinking" vs "reconnecting".
 */
export function Thinking({ label = "thinking" }: { label?: string }) {
  const [frame, setFrame] = useState(0);
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    const spin = setInterval(() => setFrame((f) => (f + 1) % FRAMES.length), 90);
    const start = Date.now();
    const clock = setInterval(() => setSecs(Math.round((Date.now() - start) / 1000)), 500);
    return () => {
      clearInterval(spin);
      clearInterval(clock);
    };
  }, []);

  return (
    <Text color="yellow">
      {FRAMES[frame]} {label}…{secs > 0 ? ` ${secs}s` : ""}
    </Text>
  );
}
