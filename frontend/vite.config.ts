import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The production build lands in backend/app/static, which FastAPI serves directly,
// so `uvicorn app.main:app` is all that is needed to run the whole thing.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/app/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
