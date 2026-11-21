import { realpathSync } from "node:fs";
import { registerHooks } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const islands = realpathSync(join(process.env.FRAPPE_BENCH_ROOT, "apps/frappe/ui/vite/island"));
const entry = pathToFileURL(join(islands, "build-pages.js")).href;
const pages = JSON.stringify(pathToFileURL(join(islands, "pages.js")).href);
const source = `
	import { realpathSync } from "node:fs";
	import { discoverPageIslands as discover } from ${pages};
	export * from ${pages};

	export function discoverPageIslands(...args) {
		const pages = discover(...args);
		// Rolldown resolves the importer to its real path before matching these roots.
		return { ...pages, dirs: pages.dirs.map((dir) => realpathSync(dir)) };
	}
`;

registerHooks({
	resolve(specifier, context, next) {
		if (context.parentURL === entry && specifier === "./pages.js") {
			return { url: "data:text/javascript," + encodeURIComponent(source), shortCircuit: true };
		}
		return next(specifier, context);
	},
});
