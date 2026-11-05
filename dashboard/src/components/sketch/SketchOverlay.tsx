import { useEffect, useMemo, useRef, useState } from 'react';
import { useSketchStore } from '../../store/sketch-store';
import { pathD, viewBoxFor, type Viewport } from './geometry';

/**
 * The saved sketch as a thin, read-only overlay over the View stage. Nothing
 * here takes input: the view underneath stays the one being turned and
 * zoomed. Drawing happens only in Sketch mode.
 */
export function SketchOverlay({ projectId }: { projectId: string }) {
  const strokes = useSketchStore((s) => s.docs[projectId]?.strokes);
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Viewport>({ width: 1, height: 1 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setSize({ width: r.width, height: r.height });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const paths = useMemo(() => (strokes ?? []).map((s) => ({ id: s.id, d: pathD(s.points) })), [strokes]);

  return (
    <div ref={ref} className="cv-sketch-overlay" data-testid="sketch-overlay" aria-hidden="true">
      <svg viewBox={viewBoxFor(size)} preserveAspectRatio="xMidYMid meet">
        {paths.map((p) => (
          <path key={p.id} d={p.d} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
    </div>
  );
}
