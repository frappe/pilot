import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'

export default defineConfig({
  plugins: [vue(), Icons({ compiler: 'vue3', autoInstall: true })],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  base: '/editor-assets/',
  build: {
    outDir: '../../backend/static/editor',
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 4000,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
          monaco: ['monaco-editor'],
          frappe: ['frappe-ui'],
        },
      },
    },
  },
  optimizeDeps: {
    exclude: ['frappe-ui'],
    include: ['monaco-editor'],
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
