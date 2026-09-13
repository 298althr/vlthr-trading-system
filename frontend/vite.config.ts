import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const buildTimestamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(0, 12)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        entryFileNames: `assets/[name]-[hash]-${buildTimestamp}.js`,
        chunkFileNames: `assets/[name]-[hash]-${buildTimestamp}.js`,
        assetFileNames: `assets/[name]-[hash]-${buildTimestamp}[extname]`,
      },
    },
  },
})
