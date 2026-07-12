import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // Served under rapidreview.io/merv in production; assets resolve from /merv/.
  base: '/merv/',
  plugins: [react()],
  server: {
    // PORT lets a managed preview run alongside a manually-started dev server.
    // RSUI_API points the proxy at a non-default backend (e.g. a working-tree
    // daemon on another port, leaving the long-lived 8787 daemon untouched).
    port: Number(process.env.PORT) || 5173,
    proxy: {
      '/api': { target: process.env.RSUI_API || 'http://127.0.0.1:8787', changeOrigin: true },
      '/health': { target: process.env.RSUI_API || 'http://127.0.0.1:8787', changeOrigin: true },
    },
  },
});
