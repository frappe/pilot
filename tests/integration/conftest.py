from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Can be overridden in CI: BENCH_TEST_ROOT=/tmp/ci-bench pytest tests/integration/
_DEFAULT_BENCH_ROOT = Path(__file__).parent.parent.parent / "benches" / "test-bench"
BENCH_TEST_ROOT = Path(os.environ.get("BENCH_TEST_ROOT", _DEFAULT_BENCH_ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a fully initialised Frappe bench (run bench init first)",
    )
    config.addinivalue_line(
        "markers",
        "production: deploys to production and manages its own redis - run "
        "separately from tests that rely on an externally-started redis.",
    )


@pytest.fixture(scope="session")
def bench_root() -> Path:
    if not (BENCH_TEST_ROOT / "bench.toml").exists():
        pytest.skip(
            f"No bench.toml at {BENCH_TEST_ROOT}. "
            "Run 'bench init' inside that directory first, or set BENCH_TEST_ROOT."
        )
    # bench-cli installs no `bench` into the bench venv; probe the interpreter.
    if not (BENCH_TEST_ROOT / "env" / "bin" / "python").exists():
        pytest.skip(
            f"Bench env not initialised at {BENCH_TEST_ROOT}. Run 'bench init' inside that directory first."
        )
    return BENCH_TEST_ROOT


@pytest.fixture(scope="session")
def bench_bin() -> str:
    b = shutil.which("bench")
    if b is None:
        pytest.skip("'bench' binary not found in PATH - install bench-cli first")
    return b


@pytest.fixture(scope="session")
def site_name() -> str:
    return "site1.localhost"


@pytest.fixture(scope="session")
def testapp_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a local testapp git repo for get-app tests."""
    src = Path(__file__).parent.parent / "fixtures" / "testapp"
    base = tmp_path_factory.mktemp("repos")
    repo = base / "testapp"
    shutil.copytree(src, repo)

    # git init with 'main' as the default branch (git ≥ 2.28)
    result = subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    if result.returncode != 0:
        # older git: init then rename branch
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, check=True, capture_output=True)

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=ci@test.com",
            "-c",
            "user.name=CI",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo
