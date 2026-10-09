import { defineConfig } from 'vite';

// Local development only: this is not a public deployment or an authentication proxy.
export default defineConfig({
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: {
      '/mcp': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/oauth/google/events': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/oauth/google/appointments': { target: 'http://127.0.0.1:8080', changeOrigin: true },
    },
  },
  preview: { host: '127.0.0.1' },
});
