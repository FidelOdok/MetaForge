import { Monitor, Moon, Sun } from 'lucide-react';
import { isThemeMode, useThemeStore } from '../../store/theme-store';

interface ThemeToggleProps {
  /** Icon-only chip used in the topbar. */
  compact?: boolean;
}

/** Light / Dark / System appearance control, persisted to localStorage. */
export function ThemeToggle({ compact = false }: ThemeToggleProps) {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);
  const Icon = mode === 'system' ? Monitor : mode === 'dark' ? Moon : Sun;

  return (
    <label
      className={`theme-control ${compact ? 'is-compact' : ''}`}
      title={`Appearance: ${mode}`}
    >
      <Icon size={17} aria-hidden="true" />
      <span className="sr-only">Appearance</span>
      <select
        aria-label="Appearance"
        value={mode}
        onChange={(e) => {
          if (isThemeMode(e.target.value)) setMode(e.target.value);
        }}
      >
        <option value="light">Light</option>
        <option value="dark">Dark</option>
        <option value="system">System</option>
      </select>
    </label>
  );
}
