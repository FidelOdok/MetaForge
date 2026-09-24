import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './index.css';

// Sync the theme store with the persisted Light / Dark / System preference
// (index.html already applied it before first paint).
import './store/theme-store';

// Observability — init before React render.
// RUM ships to collectors at the same-origin paths /faro and /otlp. Those
// routes exist only where a reverse proxy provides them, which in practice
// means the bundled Docker image's nginx.conf. Initialising RUM anywhere else
// just spams 404s (MET-507).
//
// This used to key off `import.meta.env.PROD`, on the assumption that a
// production build implies that proxy. Hosting the dashboard statically —
// Vercel — breaks the assumption: the build is PROD, the collector routes are
// not there, and every page load 404s twice. So the switch is now explicit and
// off by default; `dashboard/Dockerfile` sets it for the one image that does
// serve those paths.
import { initFaro } from './lib/faro';
import { initTelemetry } from './lib/telemetry';
import { initWebVitals } from './lib/web-vitals';

const rumEnabled = import.meta.env.VITE_RUM_ENABLED === 'true';
if (rumEnabled) {
  initFaro();
  initTelemetry();
  initWebVitals();
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
