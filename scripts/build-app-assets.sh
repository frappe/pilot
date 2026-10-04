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
import sys
import tomllib

try:
    with open(sys.argv[1], "rb") as f:
        assets = tomllib.load(f).get("tool", {}).get("bench", {}).get("assets", [])
except FileNotFoundError:
    assets = []
for spa in [assets] if isinstance(assets, dict) else assets:
    keys = ("build_dir", "out_dir", "index_html_path")
    print(" ".join(str(spa.get(key, "")).removeprefix("./") or "-" for key in keys))
EOF
}
mapfile -t spas < <(spa_settings)
has_build_script() {
    python3 -c 'import json, sys; sys.exit("build" not in json.load(open(sys.argv[1])).get("scripts", {}))' \
        "$app_dir/package.json" 2>/dev/null
}
if [ ${#spas[@]} -eq 0 ] && has_build_script; then
    echo "error: $app has a build script but declares no [tool.bench.assets]; declare each SPA it builds" >&2
    exit 1
fi

echo "==> Building $app at $commit against frappe $frappe_branch"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bench="$work/bench"
mkdir -p "$bench/apps" "$bench/sites/assets"
git clone --quiet --depth 1 --branch "$frappe_branch" https://github.com/frappe/frappe "$bench/apps/frappe"
# A copy, not a symlink: frappe-ui finds the app by walking up the real path to apps/.
cp -a "$app_dir" "$bench/apps/$app"
app_dir="$bench/apps/$app"
printf 'frappe\n%s\n' "$app" > "$bench/sites/apps.txt"
# esbuild writes into sites/assets/<app>/dist; Frappe links that to the app's public/.
for name in frappe "$app"; do
    ln -s "$bench/apps/$name/$name/public" "$bench/sites/assets/$name"
done
# Some SPAs import their dev ports from here at build time; these are Frappe's defaults.
echo '{"webserver_port": 8000, "socketio_port": 9000}' > "$bench/sites/common_site_config.json"

echo "==> Installing JS dependencies"
(cd "$bench/apps/frappe" && yarn install --frozen-lockfile --silent)
build_dirs=("")
for spa in "${spas[@]}"; do
    read -r build_dir _ _ <<< "$spa"
    [ "$build_dir" != "-" ] && build_dirs+=("$build_dir")
done
for dir in "${build_dirs[@]}"; do
    if [ -f "$app_dir/${dir:+$dir/}package.json" ]; then
        (cd "$app_dir/$dir" && yarn install --frozen-lockfile --silent)
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
if [ ${#paths[@]} -eq 0 ]; then
    echo "error: $app has no assets to publish" >&2
    exit 1
fi

echo "==> Packing"
python3 - "$app" "$commit" "${paths[@]}" > "$work/manifest.json" <<'EOF'
import json
import sys

app, commit, *paths = sys.argv[1:]
print(json.dumps({"app": app, "commit": commit, "paths": paths}, indent=1))
EOF
archive="$app-$commit.tar.gz"
tar -czf "$out_dir/$archive" -C "$work" manifest.json -C "$app_dir" "${paths[@]}"
(cd "$out_dir" && sha256sum "$archive" > "$archive.sha256")
echo "==> Wrote $out_dir/$archive"
