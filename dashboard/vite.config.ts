import { defineConfig } from 'vite';

// Local development only: this is not a public deployment or an authentication proxy.
export default defineConfig({
  server: {
    // Cognito local redirect is http://localhost:5173/... — bind the name Cognito expects.
    host: 'localhost', port: 5173, strictPort: true,
    proxy: {
      '/household/manage': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/household/calendar': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/household/notify': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/local/household': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/mcp': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/oauth/google/events': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/oauth/google/appointments': { target: 'http://127.0.0.1:8080', changeOrigin: true },
    },
  },
  preview: { host: 'localhost' },
});
