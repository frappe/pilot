import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import autoprefixer from 'autoprefixer'
import tailwindcss from 'tailwindcss'
import Icons from 'unplugin-icons/vite'

export default defineConfig({
  plugins: [vue(), Icons({ compiler: 'vue3', autoInstall: true })],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  css: {
    postcss: { plugins: [tailwindcss(), autoprefixer()] },
  },
  base: '/editor-assets/',
  build: {
    outDir: '../../backend/static/editor',
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'frappe', test: /node_modules[\\/]frappe-ui[\\/]/ },
            { name: 'vue', test: /node_modules[\\/](vue|vue-router|pinia)[\\/]/ },
          ],
        },
      },
    },
  },
  optimizeDeps: {
    exclude: ['frappe-ui'],
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
