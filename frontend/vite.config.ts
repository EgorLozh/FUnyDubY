import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Сборка кладёт статику в dist (её отдаёт nginx), dev-сервер проксирует API на бэкенд.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // локальная разработка: бэкенд на сервере проброшен на этот порт
        target: 'http://127.0.0.1:8091',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
  },
})
