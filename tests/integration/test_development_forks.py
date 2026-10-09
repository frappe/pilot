from __future__ import annotations

import json
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from pilot.core.bench import Bench
from pilot.core.bench.cloning.runtime import ForkRuntime

pytestmark = pytest.mark.integration


def test_two_forks_restore_independent_sites_and_import_own_framework(bench_root, site_name, tmp_path):
    source = Bench(bench_root)
    if source.config.production.enabled:
        pytest.skip("Requires a development fixture bench.")
    for app in source.apps():
        status = subprocess.run(
            ["git", "-C", str(app.path), "status", "--porcelain"], check=True, capture_output=True
        )
        if status.stdout.strip():
            pytest.skip("Commit source changes before preparing a template.")
    template = tmp_path / "template"
    source.site(site_name).prepare_template(template)
    suffix = uuid.uuid4().hex[:8]
    names = [f"fork-{suffix}-a", f"fork-{suffix}-b"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(source.fork, name, template) for name in names]
        forks = [future.result() for future in futures]
    databases = []
    for bench in forks:
        site = bench.site(f"{bench.config.name}.localhost")
        with ForkRuntime(bench):
            script = (
                "import json,frappe; "
                f"frappe.init({site.config.name!r},sites_path={str(bench.sites_path)!r}); "
                "frappe.connect(); "
                "print(json.dumps({'framework':frappe.__file__,'db':frappe.conf.db_name,"
                "'doctypes':frappe.db.count('DocType')})); frappe.destroy()"
            )
            result = subprocess.run(
                [str(bench.python), "-c", script],
                check=True,
                capture_output=True,
                text=True,
                cwd=bench.sites_path,
            )
            state = json.loads(result.stdout.splitlines()[-1])
            assert str(bench.apps_path) in state["framework"]
            assert state["doctypes"] > 0
            databases.append(state["db"])
            site.migrate()
    assert len(set(databases)) == 2
    assert forks[0].config.http_port != forks[1].config.http_port
