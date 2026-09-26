import { defineConfig } from 'vitest/config';

/**
 * Node environment, not jsdom: the claims guard reads the shipped files off
 * disk rather than rendering a component tree. There is no DOM to simulate.
 */
export default defineConfig({
  test: {
    environment: 'node',
    globals: true,
    include: ['src/**/*.test.ts'],
  },
});
