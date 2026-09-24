// Appearance store. The index.html bootstrap script applies the persisted
// theme before first paint; this store keeps it in sync at runtime (theme
// control, OS preference changes while in "system", other tabs).
import { create } from 'zustand';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'metaforge.theme';

export function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system';
}

function readStoredMode(): ThemeMode {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeMode(value) ? value : 'light';
  } catch {
    return 'light';
  }
}

const darkQuery =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === 'system') return darkQuery?.matches ? 'dark' : 'light';
  return mode;
}

/** Apply a mode to <html> (dark class, data-theme, color-scheme). */
export function applyTheme(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode);
  if (typeof document !== 'undefined') {
    const root = document.documentElement;
    root.classList.toggle('dark', resolved === 'dark');
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
  }
  return resolved;
}

interface ThemeState {
  mode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
}

const initialMode = readStoredMode();

export const useThemeStore = create<ThemeState>((set) => ({
  mode: initialMode,
  resolvedTheme: applyTheme(initialMode),
  setMode: (mode) => {
    if (!isThemeMode(mode)) return;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // Storage unavailable (private mode): still apply for this session.
    }
    set({ mode, resolvedTheme: applyTheme(mode) });
  },
}));

darkQuery?.addEventListener?.('change', () => {
  if (useThemeStore.getState().mode === 'system') {
    useThemeStore.setState({ resolvedTheme: applyTheme('system') });
  }
});

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
    const mode = readStoredMode();
    useThemeStore.setState({ mode, resolvedTheme: applyTheme(mode) });
  });
}
