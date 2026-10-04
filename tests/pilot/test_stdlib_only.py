"""The CLI runs on the host's system Python, which has no third-party packages.
Only code that runs inside the admin venv may import them."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
PILOT = ROOT / "pilot"

# Modules that only run inside the admin venv (daemons and integrations the Admin drives).
ADMIN_VENV_IMPORTS = {
    "pilot/core/server/monitoring.py": {"psutil"},
    "pilot/core/server/monitoring_proc.py": {"psutil"},
    "pilot/core/server/monitoring_datum.py": {"datum_client"},
    "pilot/integrations/s3/base.py": {"boto3", "botocore"},
    "pilot/integrations/llm/base.py": {"litellm"},
    "pilot/integrations/llm/lite.py": {"litellm"},
    "pilot/core/database/engines/mariadb.py": {"pymysql"},
    "pilot/core/database/engines/postgres.py": {"psycopg2"},
    "pilot/managers/database/mariadb.py": {"pymysql"},
}
FIRST_PARTY = {"pilot", "admin"}


def pilot_sources() -> list[Path]:
    return [path for path in sorted(PILOT.rglob("*.py")) if "_vendor" not in path.parts]


def imported_packages(tree: ast.AST) -> set[str]:
    packages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            packages.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            packages.add(node.module.split(".")[0])
    return packages


def admin_modules_used_by_pilot() -> set[str]:
    modules = set()
    for path in pilot_sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("admin."):
                modules.add(node.module)
    return modules


def test_pilot_imports_only_the_standard_library() -> None:
    allowed_everywhere = set(sys.stdlib_module_names) | FIRST_PARTY
    violations = []
    for path in pilot_sources():
        relative = path.relative_to(ROOT).as_posix()
        allowed = allowed_everywhere | ADMIN_VENV_IMPORTS.get(relative, set())
        extra = imported_packages(ast.parse(path.read_text())) - allowed
        violations.extend(f"{relative}: {package}" for package in sorted(extra))

    assert violations == []


def test_cli_modules_import_without_site_packages() -> None:
    """`python -S` drops site-packages, as on a host where only the system Python exists."""
    admin_venv_modules = {path.removesuffix(".py").replace("/", ".") for path in ADMIN_VENV_IMPORTS}
    modules = sorted(admin_modules_used_by_pilot())
    for path in pilot_sources():
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
        if path.name != "__main__.py" and module not in admin_venv_modules:
            modules.append(module)

    script = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "failed = []\n"
        "for name in sys.argv[1:]:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ImportError as error:\n"
        "        failed.append(f'{name}: {error}')\n"
        "print('\\n'.join(failed))\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script, *modules], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == ""


def test_cli_help_runs_without_site_packages() -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "bin" / "pilot"), "--help"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
