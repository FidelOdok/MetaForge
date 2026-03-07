import { create } from 'zustand';

type ThemeMode = 'light' | 'dark' | 'system';

interface ThemeState {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
}

function applyTheme(mode: ThemeMode) {
  const root = document.documentElement;
  if (mode === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.classList.toggle('dark', prefersDark);
  } else {
    root.classList.toggle('dark', mode === 'dark');
  }
}

function getStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem('metaforge-theme');
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // localStorage unavailable
  }
  return 'system';
}

export const useThemeStore = create<ThemeState>((set) => {
  const initial = getStoredMode();
  // Apply on init
  if (typeof document !== 'undefined') {
    applyTheme(initial);
  }

  return {
    mode: initial,
    setMode: (mode) => {
      try {
        localStorage.setItem('metaforge-theme', mode);
      } catch {
        // localStorage unavailable
      }
      applyTheme(mode);
      set({ mode });
    },
  };
});

// Listen for system preference changes
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const state = useThemeStore.getState();
    if (state.mode === 'system') {
      applyTheme('system');
    }
  });
}
