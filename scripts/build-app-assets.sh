#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 3 ]; then
    echo "usage: $0 <app checkout> <frappe branch> <output dir>" >&2
    exit 2
fi

app_dir=$(realpath "$1")
frappe_branch=$2
mkdir -p "$3"
out_dir=$(realpath "$3")

hooks=$(find "$app_dir" -mindepth 2 -maxdepth 2 -name hooks.py -not -path "*/node_modules/*" | head -n 1)
if [ -z "$hooks" ]; then
    echo "error: no <app>/hooks.py in $app_dir" >&2
    exit 1
fi
app=$(basename "$(dirname "$hooks")")
commit=$(git -C "$app_dir" rev-parse HEAD)

# [tool.bench.assets] as Frappe Cloud reads it, or an array of such tables for an app with
# several SPAs. Prints "<build_dir> <out_dir> <index_html_path>" per SPA, relative to the checkout.
spa_settings() {
    python3 - "$app_dir/pyproject.toml" <<'EOF'
import posixpath
import sys
import tomllib

try:
    with open(sys.argv[1], "rb") as f:
        assets = tomllib.load(f).get("tool", {}).get("bench", {}).get("assets", [])
except FileNotFoundError:
    assets = []
for spa in [assets] if isinstance(assets, dict) else assets:
    build_dir = posixpath.normpath(str(spa.get("build_dir", "")) or ".")
    paths = [build_dir]
    for key in ("out_dir", "index_html_path"):
        path = str(spa.get(key, ""))
        # Vite-style "../" paths are relative to build_dir, others to the checkout.
        base = build_dir if path.startswith("../") else "."
        path = posixpath.normpath(posixpath.join(base, path)) if path else ""
        if path.startswith(".."):
            sys.exit(f"error: {key} {spa[key]!r} points outside the app")
        paths.append(path)
    print(" ".join(path if path not in ("", ".") else "-" for path in paths))
EOF
}
# A substitution, so a malformed pyproject.toml stops the build.
spa_output=$(spa_settings)
spas=()
[ -n "$spa_output" ] && mapfile -t spas <<< "$spa_output"
has_build_script() {
    python3 -c 'import json, sys; sys.exit("build" not in json.load(open(sys.argv[1])).get("scripts", {}))' \
        "$app_dir/package.json" 2>/dev/null
}
# Frappe's own build script is the esbuild bundler, not a SPA.
if [ "$app" != frappe ] && [ ${#spas[@]} -eq 0 ] && has_build_script; then
    echo "error: $app has a build script but declares no [tool.bench.assets]; declare each SPA it builds" >&2
    exit 1
fi

echo "==> Building $app at $commit against frappe $frappe_branch"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bench="$work/bench"
mkdir -p "$bench/apps" "$bench/sites/assets"
# When the app is frappe itself, the checkout is the framework that gets built.
apps=(frappe)
if [ "$app" != frappe ]; then
    git clone --quiet --depth 1 --branch "$frappe_branch" https://github.com/frappe/frappe "$bench/apps/frappe"
    apps+=("$app")
fi
# A copy, not a symlink: frappe-ui finds the app by walking up the real path to apps/.
cp -a "$app_dir" "$bench/apps/$app"
app_dir="$bench/apps/$app"
printf '%s\n' "${apps[@]}" > "$bench/sites/apps.txt"
# esbuild writes into sites/assets/<app>/dist; Frappe links that to the app's public/.
for name in "${apps[@]}"; do
    ln -s "$bench/apps/$name/$name/public" "$bench/sites/assets/$name"
done
# Some SPAs import their dev ports from here at build time; these are Frappe's defaults.
echo '{"webserver_port": 8000, "socketio_port": 9000}' > "$bench/sites/common_site_config.json"

echo "==> Installing JS dependencies"
(cd "$bench/apps/frappe" && yarn install --frozen-lockfile --silent)
build_dirs=()
[ "$app" != frappe ] && build_dirs+=("")
for spa in "${spas[@]}"; do
    read -r build_dir _ _ <<< "$spa"
    [ "$build_dir" != "-" ] && build_dirs+=("$build_dir")
done
for dir in "${build_dirs[@]}"; do
    if [ -f "$app_dir/${dir:+$dir/}package.json" ]; then
        (cd "$app_dir/$dir" && yarn install --frozen-lockfile --silent)
    fi
done

# As `bench build` does: bundles import "<app>/public/node_modules/...", such as Frappe's desk.bundle.scss.
for name in "${apps[@]}"; do
    if [ -d "$bench/apps/$name/node_modules" ]; then
        ln -sfn "$bench/apps/$name/node_modules" "$bench/apps/$name/$name/public/node_modules"
    fi
done

echo "==> Building"
# FRAPPE_DOCKER_BUILD skips the Redis cache refresh; there is no Redis here.
(cd "$bench/apps/frappe" && FRAPPE_DOCKER_BUILD=1 yarn run production --apps "$app" --run-build-command)

# esbuild bundles are optional (an app may only ship a SPA); declared SPA paths are not.
paths=()
[ -d "$app_dir/$app/public/dist" ] && paths+=("$app/public/dist")
for spa in "${spas[@]}"; do
    read -r _ out index <<< "$spa"
    for path in "$out" "$index"; do
        [ "$path" = "-" ] && continue
        if [ ! -e "$app_dir/$path" ]; then
            echo "error: the build did not produce $path" >&2
            exit 1
        fi
        paths+=("$path")
    done
done
# Ignored files the build writes outside dist and the SPAs, such as a generated tailwind.css.
while IFS= read -r path; do
    path=${path%/}
    case "$path" in */node_modules | */node_modules/*) continue ;; esac
    covered=
    for published in "${paths[@]}"; do
        case "$path" in "$published" | "$published"/*) covered=1 ;; esac
    done
    [ -z "$covered" ] && paths+=("$path")
done < <(git -C "$app_dir" ls-files --others --ignored --exclude-standard --directory -- "$app/public" "$app/www")

if [ ${#paths[@]} -eq 0 ]; then
    # Only an app with nothing to build may publish nothing.
    if [ -f "$app_dir/package.json" ] || [ -n "$(find "$app_dir/$app/public" -name '*.bundle.*' -print -quit 2>/dev/null)" ]; then
        echo "error: $app has no assets to publish" >&2
        exit 1
    fi
    echo "==> $app has no assets; publishing an empty archive"
fi

echo "==> Packing"
# The app's assets.json entries, including keys file names do not tell (islands).
python3 - "$app" "$commit" "$bench/sites/assets" "${paths[@]}" > "$work/manifest.json" <<'EOF'
import json
import sys
from pathlib import Path

app, commit, assets_dir, *paths = sys.argv[1:]
manifest = {"app": app, "commit": commit, "paths": paths}
# Page islands belong to each bench, not to the archive.
page_islands = "/assets/frappe/dist/page-island/"
for name in ("assets.json", "assets-rtl.json"):
    source = Path(assets_dir) / name
    entries = json.loads(source.read_text()) if source.exists() else {}
    manifest[name] = {
        key: url for key, url in entries.items() if url.startswith(f"/assets/{app}/") and not url.startswith(page_islands)
    }
print(json.dumps(manifest, indent=1))
EOF
archive="$app-$commit.tar.gz"
tar_args=(--exclude=frappe/public/dist/page-island -C "$work" manifest.json)
[ ${#paths[@]} -gt 0 ] && tar_args+=(-C "$app_dir" "${paths[@]}")
tar -czf "$out_dir/$archive" "${tar_args[@]}"
(cd "$out_dir" && sha256sum "$archive" > "$archive.sha256")
echo "==> Wrote $out_dir/$archive"
