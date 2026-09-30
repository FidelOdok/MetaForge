import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    css: false,
    exclude: ['e2e/**', 'node_modules/**'],
    // FORGE-389: on a checkout served over a slow filesystem (a WSL2
    // `/mnt/c` mount, a network share) some workers exceed vitest's
    // worker-start timeout and their files are dropped:
    //
    //   [vitest-pool]: Failed to start forks worker for test files X
    //   Caused by: Timeout waiting for worker to respond
    //
    // That timeout is a hardcoded 60s constant in vitest, not a setting,
    // and neither capping `maxForks` nor switching to the threads pool
    // changed it -- both were measured here and both still dropped files.
    // So this config deliberately does NOT try to tune it. `npm test`
    // runs `scripts/check-suite.mjs`, which fails loudly when fewer files
    // ran than exist, because the summary line alone does not say so.
  },
});
