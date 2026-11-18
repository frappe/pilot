import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.site import RELOCATE_RENAMED_BLOBS_METHOD
from pilot.exceptions import BenchError
from tests.pilot.core.test_site_rename import _site
from tests.pilot.integrations.test_central_client import _bench

SITE = "new.example.com"


def _stats(**counts) -> str:
    return json.dumps({"moved": 0, "bytes": 0, "skipped": 0, "errors": 0, **counts})


def _relocate(tmp_path: Path, stdout: str, **config):
    bench = _bench(tmp_path)
    _site(bench, SITE, **config)
    result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    with patch("pilot.core.site.run_command", return_value=result) as run_command:
        bench.site(SITE).relocate_renamed_blobs()
    return bench, run_command


def test_an_s3_site_runs_the_frappe_relocation(tmp_path: Path) -> None:
    """Frappe owns the stored files, so Pilot only asks the site to move them; `execute`
    prints the result on its last line, after any warnings."""
    bench, run_command = _relocate(tmp_path, f"a warning\n{_stats(moved=2)}\n", storage_driver="s3")

    argv = run_command.call_args.args[0]
    assert argv[-5:] == ["frappe", "--site", SITE, "execute", RELOCATE_RENAMED_BLOBS_METHOD]
    assert run_command.call_args.kwargs["cwd"] == bench.sites_path


def test_a_site_not_on_s3_is_left_alone(tmp_path: Path) -> None:
    """Without S3 there is nothing to move, and older Frappe has no relocation method to call."""
    _, run_command = _relocate(tmp_path, "", storage_driver="local")

    run_command.assert_not_called()


def test_skipped_rows_do_not_fail(tmp_path: Path) -> None:
    """A blob whose bytes live in another bucket is skipped by design, not an error."""
    _relocate(tmp_path, _stats(skipped=3), storage_driver="s3")


@pytest.mark.parametrize("stdout", [_stats(moved=1, errors=2), ""])
def test_failed_or_missing_results_raise(tmp_path: Path, stdout: str) -> None:
    """Errors must reach the rename, which then stays pending so a retry moves the rest."""
    with pytest.raises(BenchError):
        _relocate(tmp_path, stdout, storage_driver="s3")
