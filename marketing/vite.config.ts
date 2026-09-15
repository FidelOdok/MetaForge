import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5174, // 5173 belongs to the dashboard dev server
    watch: {
      // WSL2: inotify events don't cross the Windows <-> Linux boundary.
      usePolling: !!process.env.CHOKIDAR_USEPOLLING,
      interval: 1000,
    },
  },
});
