import { useThemeStore } from '../../store/theme-store';

const MODES = ['light', 'dark', 'system'] as const;

const ICONS: Record<(typeof MODES)[number], string> = {
  light: '\u2600',   // Sun
  dark: '\uD83C\uDF19',    // Moon
  system: '\uD83D\uDCBB',  // Monitor
};

const LABELS: Record<(typeof MODES)[number], string> = {
  light: 'Light',
  dark: 'Dark',
  system: 'System',
};

export function ThemeToggle() {
  const { mode, setMode } = useThemeStore();

  function cycle() {
    const idx = MODES.indexOf(mode);
    const next = MODES[(idx + 1) % MODES.length]!;
    setMode(next);
  }

  return (
    <button
      type="button"
      onClick={cycle}
      title={`Theme: ${LABELS[mode]}`}
      className="flex h-8 w-8 items-center justify-center rounded-md text-sm text-zinc-500 transition-colors hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
    >
      {ICONS[mode]}
    </button>
  );
}
