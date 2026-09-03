import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/auth': 'http://localhost:8000',
      '/sdk': 'http://localhost:8000',
      '/agents': 'http://localhost:8000',
      '/evaluations': 'http://localhost:8000',
      '/test-suites': 'http://localhost:8000',
      '/experiments': 'http://localhost:8000',
      '/providers': 'http://localhost:8000',
      '/gateway': 'http://localhost:8000',
    }
  }
})
