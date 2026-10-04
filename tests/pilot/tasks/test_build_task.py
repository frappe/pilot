from types import SimpleNamespace

from pilot.tasks.build import BuildTask


def _task(tmp_path, app=None):
    calls = []
    bench = SimpleNamespace(rebuild_assets=lambda **kwargs: calls.append(kwargs))
    return BuildTask(bench=bench, bench_root=tmp_path, app=app), calls


def test_build_forces_a_full_rebuild_for_the_whole_bench(tmp_path):
    task, calls = _task(tmp_path)
    task.build()
    assert calls == [{"apps": None, "force": True}]


def test_build_forces_a_full_rebuild_for_one_app(tmp_path):
    task, calls = _task(tmp_path, app="erpnext")
    task.build()
    assert calls == [{"apps": ["erpnext"], "force": True}]


def test_build_for_a_site_delegates_to_the_site(tmp_path):
    built = []
    bench = SimpleNamespace(site=lambda name: SimpleNamespace(build_assets=lambda: built.append(name)))
    BuildTask(bench=bench, bench_root=tmp_path, site="a.localhost").build()
    assert built == ["a.localhost"]


def test_a_site_builds_the_apps_it_runs():
    from pilot.core.site import Site

    calls = []
    site = SimpleNamespace(
        active_apps=lambda: ["frappe", "erpnext"],
        bench=SimpleNamespace(rebuild_assets=lambda **kwargs: calls.append(kwargs)),
    )
    Site.build_assets(site)
    assert calls == [{"apps": ["frappe", "erpnext"], "force": True}]
