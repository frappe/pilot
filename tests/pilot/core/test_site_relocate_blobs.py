import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.site import RELOCATE_RENAMED_BLOBS_METHOD
from pilot.exceptions import BenchError
from pilot.tasks.relocate_renamed_blobs import RelocateRenamedBlobsTask
from tests.pilot.core.test_site_rename import _site
from tests.pilot.integrations.test_central_client import _bench

SITE = "new.example.com"


def _stats(**counts) -> str:
    return json.dumps({"moved": 0, "bytes": 0, "skipped": 0, "errors": 0, **counts})


def _relocate(tmp_path: Path, stdout: str):
    bench = _bench(tmp_path)
    _site(bench, SITE, storage_driver="s3")
    result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    with patch("pilot.core.site.run_command", return_value=result) as run_command:
        bench.site(SITE).relocate_renamed_blobs()
    return bench, run_command


def test_relocation_runs_the_frappe_method(tmp_path: Path) -> None:
    """Frappe owns the stored files, so Pilot only asks the site to move them; `execute`
    prints the result on its last line, after any warnings."""
    bench, run_command = _relocate(tmp_path, f"a warning\n{_stats(moved=2)}\n")

    argv = run_command.call_args.args[0]
    assert argv[-5:] == ["frappe", "--site", SITE, "execute", RELOCATE_RENAMED_BLOBS_METHOD]
    assert run_command.call_args.kwargs["cwd"] == bench.sites_path


def test_skipped_rows_do_not_fail(tmp_path: Path) -> None:
    """A blob whose bytes live in another bucket is skipped by design, not an error."""
    _relocate(tmp_path, _stats(skipped=3))


@pytest.mark.parametrize("stdout", [_stats(moved=1, errors=2), ""])
def test_failed_or_missing_results_raise(tmp_path: Path, stdout: str) -> None:
    """Errors fail the relocation task, which can be retried on its own."""
    with pytest.raises(BenchError):
        _relocate(tmp_path, stdout)


@pytest.mark.parametrize(("config", "expected"), [({"storage_driver": "s3"}, True), ({}, False)])
def test_has_s3_storage_reads_the_site_driver(tmp_path: Path, config: dict, expected: bool) -> None:
    bench = _bench(tmp_path)
    _site(bench, SITE, **config)

    assert bench.site(SITE).has_s3_storage is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (None, False),
        ("def relocate_blobs():\n", False),
        ("def relocate_renamed_site_blobs() -> dict:\n", True),
    ],
)
def test_relocation_needs_a_frappe_that_has_the_method(
    tmp_path: Path, source: str | None, expected: bool
) -> None:
    """Pilot can run against an older Frappe; the method's presence decides whether to call it."""
    bench = _bench(tmp_path)
    relocate = bench.apps_path / "frappe" / "frappe" / "storage" / "relocate.py"
    if source is not None:
        relocate.parent.mkdir(parents=True)
        relocate.write_text(source)

    assert bench.has_renamed_blob_relocation is expected


def test_the_task_relocates_the_site(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    task = RelocateRenamedBlobsTask(bench=bench, bench_root=tmp_path, site=SITE)

    with patch("pilot.core.site.Site.relocate_renamed_blobs", autospec=True) as relocate:
        task.run()

    assert relocate.call_args.args[0].config.name == SITE
