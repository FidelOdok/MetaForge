import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { execSync } from 'node:child_process';
import dns from 'node:dns';
import path from 'path';

// Force IPv4-first resolution so http-proxy doesn't fail with ECONNREFUSED
// when Docker DNS and Node 20's Happy Eyeballs interact badly.
dns.setDefaultResultOrder('ipv4first');

// Resolve VITE_API_URL at startup; fallback to localhost for local dev.
// When running in Docker, resolve the hostname to an IPv4 address to avoid
// http-proxy ECONNREFUSED issues with Node 20's autoSelectFamily.
let apiTarget = process.env.VITE_API_URL || 'http://localhost:8000';
try {
  const targetUrl = new URL(apiTarget);
  if (targetUrl.hostname !== 'localhost' && targetUrl.hostname !== '127.0.0.1') {
    const ip = execSync(`getent hosts ${targetUrl.hostname} | awk '{print $1}'`, {
      encoding: 'utf-8',
    }).trim();
    if (ip) {
      apiTarget = `${targetUrl.protocol}//${ip}:${targetUrl.port}`;
    }
  }
} catch {
  /* keep original target */
}
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    watch: {
      // Enable polling for WSL2 — inotify events don't cross the
      // Windows ↔ Linux filesystem boundary.
      usePolling: !!process.env.CHOKIDAR_USEPOLLING,
      interval: 1000,
    },
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (p: string) => p.replace(/^\/api/, ''),
      },
      '/ws': {
        target: apiTarget.replace('http', 'ws'),
        ws: true,
      },
    },
  },
});
