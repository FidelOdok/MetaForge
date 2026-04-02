import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { execSync } from 'node:child_process';
import dns from 'node:dns';
import path from 'path';

// Force IPv4-first resolution so http-proxy doesn't fail with ECONNREFUSED
// when Docker DNS and Node 20's Happy Eyeballs interact badly.
dns.setDefaultResultOrder('ipv4first');

// Resolve a proxy target hostname to IPv4 to avoid ECONNREFUSED with
// Node 20's autoSelectFamily / Happy Eyeballs when running in Docker.
function resolveTarget(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.hostname !== 'localhost' && parsed.hostname !== '127.0.0.1') {
      const ip = execSync(`getent hosts ${parsed.hostname} | awk '{print $1}'`, {
        encoding: 'utf-8',
      }).trim();
      if (ip) return `${parsed.protocol}//${ip}:${parsed.port}`;
    }
  } catch {
    /* keep original */
  }
  return url;
}

const apiTarget = resolveTarget(process.env.VITE_API_URL || 'http://localhost:8000');
const faroTarget = resolveTarget(process.env.VITE_FARO_URL || 'http://localhost:12347');

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 3000,
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
      '/faro': {
        target: faroTarget,
        changeOrigin: true,
        rewrite: (p: string) => p.replace(/^\/faro/, ''),
      },
    },
  },
});
