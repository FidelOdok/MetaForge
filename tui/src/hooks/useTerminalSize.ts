import { useEffect, useState } from "react";
import { useStdout } from "ink";

/** Live terminal dimensions, updated on resize — used to render a full-height
 * frame so the input pins to the bottom row. */
export function useTerminalSize(): { rows: number; cols: number } {
  const { stdout } = useStdout();
  const [size, setSize] = useState({ rows: stdout?.rows ?? 24, cols: stdout?.columns ?? 80 });
  useEffect(() => {
    if (!stdout) return;
    const onResize = () => setSize({ rows: stdout.rows ?? 24, cols: stdout.columns ?? 80 });
    stdout.on("resize", onResize);
    onResize();
    return () => {
      stdout.off("resize", onResize);
    };
  }, [stdout]);
  return size;
}
