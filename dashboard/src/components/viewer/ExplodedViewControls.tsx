import { useCallback, useEffect } from 'react';
import { RotateCcw } from 'lucide-react';
import { useViewerStore } from '../../store/viewer-store';

export function ExplodedViewControls() {
  const explodeFactor = useViewerStore((s) => s.explodeFactor);
  const setExplodeFactor = useViewerStore((s) => s.setExplodeFactor);

  const percentage = Math.round(explodeFactor * 100);

  const handleReset = useCallback(() => setExplodeFactor(0), [setExplodeFactor]);

  // Keyboard shortcut: E toggles between 0% and 50%
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'e' || e.key === 'E') {
        // Don't trigger if user is typing in an input
        if (
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLTextAreaElement ||
          e.target instanceof HTMLSelectElement
        ) {
          return;
        }
        setExplodeFactor(explodeFactor > 0 ? 0 : 0.5);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [explodeFactor, setExplodeFactor]);

  return (
    <div className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-3 rounded-lg border border-zinc-200 bg-white/90 px-4 py-2 shadow-sm backdrop-blur dark:border-zinc-700 dark:bg-zinc-800/90">
      <span className="text-xs font-medium text-zinc-500">Explode</span>

      <input
        type="range"
        min={0}
        max={100}
        value={percentage}
        onChange={(e) => setExplodeFactor(Number(e.target.value) / 100)}
        className="h-1.5 w-32 cursor-pointer accent-blue-500"
      />

      <span className="w-8 text-right text-xs font-mono text-zinc-600 dark:text-zinc-400">
        {percentage}%
      </span>

      <button
        type="button"
        onClick={handleReset}
        disabled={explodeFactor === 0}
        className="rounded p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-30 dark:hover:bg-zinc-700 dark:hover:text-zinc-200"
        title="Reset (E)"
      >
        <RotateCcw size={14} />
      </button>
    </div>
  );
}
