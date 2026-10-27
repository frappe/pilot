#!/usr/bin/env bash
set -euo pipefail

mkdir -p package-benchmark
python3 - <<'PY'
from pathlib import Path
source = Path("install.sh").read_text()
Path("/tmp/pilot-installer-functions.sh").write_text(source.split("# ── run ─")[0])
PY
# shellcheck disable=SC1091
source /tmp/pilot-installer-functions.sh

require_supported_release
ensure_curl
add_distro_repos
pkg_update

# Use the installer's own package selection, including optional repository packages.
packages=$(
    pkg_install() { printf '%s\n' "$@"; }
    bootstrap_packages
    install_database_engines
    install_production_packages
)
printf '%s\n' "$packages" | sort -u > package-benchmark/requested-packages.txt
readarray -t requested < package-benchmark/requested-packages.txt
sudo env DEBIAN_FRONTEND=noninteractive apt-get install --download-only -y "${requested[@]}"

dpkg-query -W -f='${Package}\t${Version}\t${Status}\n' > package-benchmark/packages-before.txt
export MODE
started=$(python3 -c 'import time; print(time.monotonic())')
if [ "$MODE" = before ]; then
    bootstrap_packages
    enable_cron
    install_database_engines
    install_production_packages
else
    pkg_install "${requested[@]}"
    enable_cron
fi 2>&1 | tee package-benchmark/install.log
export STARTED="$started"
python3 - <<'PY'
import json, os, time
from pathlib import Path
result = {"mode": os.environ["MODE"], "seconds": time.monotonic() - float(os.environ["STARTED"]),
          "image": os.environ.get("ImageVersion"), "runner_image": os.environ.get("ImageOS")}
Path("package-benchmark/result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
PY
dpkg-query -W -f='${Package}\t${Version}\t${Status}\n' > package-benchmark/packages-after.txt
dpkg --audit | tee package-benchmark/dpkg-audit.txt
test ! -s package-benchmark/dpkg-audit.txt
