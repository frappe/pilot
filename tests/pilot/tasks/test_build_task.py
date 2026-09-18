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
