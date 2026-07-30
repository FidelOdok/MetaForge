import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { execSync } from 'node:child_process';
import dns from 'node:dns';
import path from 'path';

// Force IPv4-first resolution so http-proxy doesn't fail with ECONNREFUSED
// when Docker DNS and Node 20's Happy Eyeballs interact badly.
dns.setDefaultResultOrder('ipv4first');

// Resolve VITE_API_URL's hostname to a raw IPv4 address -- http-proxy fails
// with ECONNREFUSED against a bare Docker-internal hostname on Node 20+
// (autoSelectFamily/Happy-Eyeballs interacting badly with http-proxy's own
// connection setup). Returns the URL unchanged for localhost, or on any
// resolution failure.
function resolveApiTarget(): string {
  const raw = process.env.VITE_API_URL || 'http://localhost:8000';
  try {
    const url = new URL(raw);
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return raw;
    const ip = execSync(`getent hosts ${url.hostname} | awk '{print $1}'`, {
      encoding: 'utf-8',
    }).trim();
    return ip ? `${url.protocol}//${ip}:${url.port}` : raw;
  } catch {
    return raw;
  }
}

/**
 * Live-caught (MET-10): resolving once at Vite startup goes stale the moment
 * the gateway container is recreated (`docker compose up -d gateway` assigns
 * it a new internal IP) -- every proxied dashboard call then fails with
 * ECONNREFUSED until the dev server itself is restarted. http-proxy reads
 * `options.target` fresh on every proxied request (it's the same options
 * object handed to `httpProxy.createProxyServer`), so refreshing it in place
 * on a short interval makes the proxy self-healing without a restart.
 */
function attachLiveTarget(options: ProxyOptions, toWs = false): void {
  const refresh = () => {
    const target = resolveApiTarget();
    options.target = toWs ? target.replace('http', 'ws') : target;
  };
  refresh();
  setInterval(refresh, 5000);
}
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    // Hosts allowed to reach the dev server. Vite returns 403 for an
    // unknown Host header, which blocks access via a remote/Tailscale
    // hostname (e.g. fidel-dev). Default to localhost + fidel-dev;
    // override with VITE_ALLOWED_HOSTS="host1,host2" (MET-483).
    allowedHosts: (process.env.VITE_ALLOWED_HOSTS || 'localhost,fidel-dev')
      .split(',')
      .map((h) => h.trim())
      .filter(Boolean),
    watch: {
      // Enable polling for WSL2 — inotify events don't cross the
      // Windows ↔ Linux filesystem boundary.
      usePolling: !!process.env.CHOKIDAR_USEPOLLING,
      interval: 1000,
    },
    proxy: {
      '/api': {
        target: resolveApiTarget(),
        changeOrigin: true,
        rewrite: (p: string) => p.replace(/^\/api/, ''),
        configure: (_proxy, options) => attachLiveTarget(options),
      },
      '/ws': {
        target: resolveApiTarget().replace('http', 'ws'),
        ws: true,
        configure: (_proxy, options) => attachLiveTarget(options, true),
      },
    },
  },
});
