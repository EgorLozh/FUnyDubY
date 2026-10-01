import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Сборка кладёт статику в dist (её отдаёт nginx), dev-сервер проксирует API на бэкенд.
// Базовый путь: на сервере приложение живёт под префиксом (/dub/), локально — в корне.
// process читаем через globalThis: в сборке нет типов Node, а env достаточно.
const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {}

export default defineConfig({
  base: env.VITE_BASE_PATH || '/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    proxy: {
      '/dub/api': {
        target: 'http://127.0.0.1:8091',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/dub/, ''),
      },
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
