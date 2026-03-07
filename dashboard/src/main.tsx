import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './index.css';

// Initialize theme from localStorage / system preference
import './store/theme-store';

// Observability — init before React render
import { initTelemetry } from './lib/telemetry';
import { initWebVitals } from './lib/web-vitals';

initTelemetry();
initWebVitals();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
