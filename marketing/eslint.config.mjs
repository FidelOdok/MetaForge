// Flat config, restoring the lint step that has been dead since the eslint 9
// migration (MET-732). The `"lint": "eslint ."` script had no eslint in the
// dependencies and no config file, so it could not run at all.
//
// Evidence it *used* to run: src/components/viewer/R3FViewer.tsx carries a
// rule-specific `// eslint-disable-next-line react-hooks/exhaustive-deps`.
// Someone wrote that against a working setup, which is why react-hooks is
// included below rather than left out.
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
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'node_modules/**',
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
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
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
