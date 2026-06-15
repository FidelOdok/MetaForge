import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './index.css';

// Initialize theme from localStorage / system preference
import './store/theme-store';

// Observability — init before React render.
// RUM ships to collectors proxied at /faro and /otlp. Those routes only exist
// behind the production reverse proxy; the Vite dev server has no such proxy,
// so initialising RUM in dev just spams the console with 404s (MET-507). Gate
// on a production build, with an explicit VITE_RUM_ENABLED opt-in for dev when
// a collector is actually wired up.
import { initFaro } from './lib/faro';
import { initTelemetry } from './lib/telemetry';
import { initWebVitals } from './lib/web-vitals';

const rumEnabled =
  import.meta.env.PROD || import.meta.env.VITE_RUM_ENABLED === 'true';
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
