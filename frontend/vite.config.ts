import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    // Pre-bundle the component-probe libraries up front. When the dev server
    // discovers them lazily (first dialog opened during a test run), Vite
    // re-optimizes dependencies and full-reloads every connected page, which
    // detached elements mid-action in a parallel Playwright worker and, via
    // the in-page mock reset, removed the element under test entirely.
    include: ['@radix-ui/react-dialog', 'react-aria-components', '@base-ui/react'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
