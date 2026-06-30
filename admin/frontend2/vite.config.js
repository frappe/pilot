import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import frappeuiPlugin from 'frappe-ui/vite'

const backendProxyUrl = process.env.BACKEND_PROXY_URL

export default defineConfig(({ mode }) => ({
  plugins: [
    frappeuiPlugin({
      lucideIcons: true,
      frappeProxy: false,
      jinjaBootData: false,
      buildConfig: false,
    }),
    vue(),
  ],
  build: {
    outDir: '../backend/static/dist',
    emptyOutDir: true,
    sourcemap: mode === 'development',
    minify: mode !== 'development',
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    ...(backendProxyUrl && {
      proxy: {
        '/api': backendProxyUrl,
        '/socket.io': { target: backendProxyUrl, ws: true },
      },
    }),
  },
  optimizeDeps: {
    include: ['feather-icons', 'debug'],
    exclude: ['frappe-ui'],
  },
}))
