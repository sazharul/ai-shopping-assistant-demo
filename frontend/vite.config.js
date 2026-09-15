import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output goes to dist/.
// We set a fixed entry so the output filename is predictable.
// Copy dist/ contents into Laravel's public/chatbox/ folder after building.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: 'index.html',
    },
  },
})
