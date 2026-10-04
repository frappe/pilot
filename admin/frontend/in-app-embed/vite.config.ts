import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import frappeuiPlugin from "frappe-ui/vite";
import path from "path";

export default defineConfig({
  plugins: [
    frappeuiPlugin({
      lucideIcons: true,
      frappeProxy: false,
      jinjaBootData: false,
      buildConfig: false,
    }),

    vue({ customElement: true }),

    {
      name: "cloud-settings-dev-loader",
      apply: "serve",
      configureServer: (server) => {
        server.middlewares.use(
          "/embed/cloud-settings/cloud-settings.js",
          (request, response) => {
            const entry = `http://${request.headers.host}/src/cloud-settings/index.ts`;

            const loader = `
              frappe.cloudSettings = {
                show: async (context) => {
                  await import(${JSON.stringify(entry)});
                  frappe.cloudSettings.show(context);
                },
              };
            `;

            response.setHeader("Content-Type", "text/javascript");
            response.end(loader);
          },
        );
      },
    },
  ],

  server: {
    host: "::",
    warmup: { clientFiles: ["./src/cloud-settings/index.ts"] },
  },

  build: {
    outDir: "../../backend/static/in-app-embed/cloud-settings",
    emptyOutDir: true,
    cssCodeSplit: false,
    sourcemap: false,
    minify: true,
    assetsInlineLimit: 1024 * 1024,
    rolldownOptions: {
      input: path.resolve(__dirname, "src/cloud-settings/index.ts"),
      output: {
        format: "iife",
        name: "FrappeCloudSettingsEmbed",
        entryFileNames: "cloud-settings.js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
