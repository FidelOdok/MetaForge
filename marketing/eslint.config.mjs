// Flat config, restoring the lint step that has been dead since the eslint 9
// migration (MET-732). The `"lint": "eslint ."` script had no eslint in the
// dependencies and no config file, so it could not run at all.
//
// Deliberately the `recommended` tier, not `recommendedTypeChecked`. The
// type-checked tier needs project-wide type information and finds a great deal
// more; adopting it is a separate decision, and `tsc --noEmit` already runs in
// CI. Measured cost of this tier when it was chosen: two violations, both
// `no-require-imports`, both of which turned out to be MET-736 -- a real
// production bug where a `require()` in a Vite ESM bundle threw
// ReferenceError at runtime and a bare `catch` swallowed it. The rule paid for
// itself on its first run, which is the argument for having it.

import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      // public/ is the shipped static site: hand-written browser scripts and
      // a 44 KB stylesheet that came with the design, not source authored to
      // this project's conventions. Linting them produces 87 no-undef errors
      // for `window` and `document` and would gain nothing -- they are
      // copied verbatim to dist/ and never transformed.
      'public/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      // Config files are CJS/ESM tooling scripts, not app code.
      '*.config.js',
      '*.config.ts',
      '*.config.mjs',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.es2022 },
    },
    rules: {
    },
  },
  {
    // Tests legitimately reach for `any` when building doubles for third-party
    // shapes, and assert on internals. Keeping the main rules on for src/ is
    // where the value is.
    files: ['**/__tests__/**', '**/*.test.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
);
