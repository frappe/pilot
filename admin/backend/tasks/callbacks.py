import json
import os
import shutil


def new_site_failure_callback(meta: dict) -> None:
    site_name = meta["args"]["name"]
    site_path = os.path.join(meta["bench_root"], "sites", site_name)
    shutil.rmtree(site_path, ignore_errors=True)


def ssl_setup_failure_callback(meta: dict) -> None:
    site_name = meta["args"]["site"]
    config_path = os.path.join(meta["bench_root"], "sites", site_name, "site_config.json")
    config = json.loads(open(config_path).read())
    config["ssl"] = False
    open(config_path, "w").write(json.dumps(config, indent=1))
