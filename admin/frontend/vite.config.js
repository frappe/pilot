import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import frappeuiPlugin from 'frappe-ui/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendProxyUrl = env.BACKEND_PROXY_URL

  return {
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
          '/api': { target: backendProxyUrl, changeOrigin: true, secure: false },
          '/socket.io': { target: backendProxyUrl, ws: true, changeOrigin: true, secure: false },
        },
      }),
    },
    optimizeDeps: {
      include: ['feather-icons', 'debug'],
      exclude: ['frappe-ui'],
    },
  }
})
