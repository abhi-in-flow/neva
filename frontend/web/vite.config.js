import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// VITE_API_BASE switches between the mock server (default) and the real
// FastAPI backend at the 12:30 integration checkpoint — change env, delete nothing.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_BASE || 'http://localhost:8787',
        changeOrigin: true,
      },
      '/media': {
        target: process.env.VITE_API_BASE || 'http://localhost:8787',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist' },
}));
