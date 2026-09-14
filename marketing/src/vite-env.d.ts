/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the site's "Open the dashboard" links point. Set per Vercel environment. */
  readonly VITE_DASHBOARD_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
