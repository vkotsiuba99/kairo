import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Локальний запуск без nginx: /api і /media проксіюються на бекенд (127.0.0.1:8000),
// як це робить nginx.conf у Docker-збірці.
const backend = 'http://127.0.0.1:8000'
const proxy = { '/api': backend, '/media': backend, '/ws': { target: backend, ws: true } }

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: { proxy },
  preview: { proxy },
})
