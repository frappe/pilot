import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import frappeuiPlugin from "frappe-ui/vite";
import path from "path";

export default defineConfig(({ mode }) => ({
  define: { 'process.env.NODE_ENV': JSON.stringify(mode === 'development' ? 'development' : 'production') },
  plugins: [
    frappeuiPlugin({
      lucideIcons: true,
      frappeProxy: false,
      jinjaBootData: false,
      buildConfig: false,
    }),

    vue({ customElement: true }),

    {
      name: 'cloud-settings-inline-styles',
      enforce: 'post',
      generateBundle: (_, bundle) => {
        const styles = Object.values(bundle).filter((asset) => asset.type === 'asset' && asset.fileName.endsWith('.css'));
        const css = styles.map((asset) => asset.type === 'asset' ? String(asset.source) : '').join('\n');
        for (const chunk of Object.values(bundle)) {
          if (chunk.type === 'chunk') chunk.code = chunk.code.replace(/(["'`])__CLOUD_SETTINGS_STYLES__\1/g, JSON.stringify(css));
        }
        for (const asset of styles) delete bundle[asset.fileName];
      },
    },

    {
      name: "cloud-settings-dev-loader",
      apply: "serve",
      configureServer: (server) => {
        server.middlewares.use(
          "/embed/cloud-settings/cloud-settings.js",
          (request, response) => {
            const entry = `http://${request.headers.host}/src/cloud-settings/index.ts`;

            const loader = `
              const loaded = import(${JSON.stringify(entry)});
              if (window.frappe) {
                window.frappe.cloudSettings = {
                  show: async (context, options) => {
                    await loaded;
                    window.frappe.cloudSettings.show(context, options);
                  },
                };
              }
            `;

            response.setHeader("Content-Type", "text/javascript");
            response.end(loader);
          },
        );
      },
    },
  ],

  resolve: {
    alias: [
      { find: '@frappe/cloud-sdk/api', replacement: path.resolve(__dirname, '../cloud-sdk/src/api.ts') },
      { find: '@frappe/cloud-sdk', replacement: path.resolve(__dirname, '../cloud-sdk/src/index.ts') },
    ],
  },

  server: {
    host: "::",
    fs: { allow: [".", "../cloud-sdk"] },
    warmup: { clientFiles: ["./src/cloud-settings/index.ts"] },
  },

  build: {
    lib: mode === 'sdk' ? {
      entry: path.resolve(__dirname, 'src/cloud-settings/runtime.ts'),
      formats: ['es'],
      fileName: 'cloud-settings',
    } : undefined,
    outDir: mode === 'sdk' ? '../cloud-sdk/dist/embed' : '../../backend/static/in-app-embed/cloud-settings',
    emptyOutDir: true,
    cssCodeSplit: false,
    sourcemap: false,
    minify: true,
    assetsInlineLimit: 1024 * 1024,
    rolldownOptions: {
      input: path.resolve(__dirname, mode === 'sdk' ? 'src/cloud-settings/runtime.ts' : 'src/cloud-settings/index.ts'),
      output: {
        format: mode === 'sdk' ? 'es' : 'iife',
        name: mode === 'sdk' ? undefined : 'FrappeCloudSettingsEmbed',
        entryFileNames: "cloud-settings.js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
}));
