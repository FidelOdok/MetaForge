// Kinetic Console is always-dark. This module exists for import compatibility.
// It applies the 'dark' class on load and is a no-op thereafter.

import { create } from 'zustand';

// Ensure the dark class is always present (index.html already sets it,
// but guard against any runtime removal).
if (typeof document !== 'undefined') {
  document.documentElement.classList.add('dark');
}

interface ThemeState {
  mode: 'dark';
}

export const useThemeStore = create<ThemeState>(() => ({ mode: 'dark' }));
