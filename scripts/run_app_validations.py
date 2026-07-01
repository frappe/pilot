#!/usr/bin/env python3
"""
Validate a marketplace app against quality requirements before merging to apps_v2.json.

Checks:
  1. Repository is public on GitHub
  2. Repository has a description
  3. pyproject.toml exists at repository root
  4. [tool.bench.frappe-dependencies] declares frappe
  5. Versioning is dynamic and __version__ is declared in the app's __init__.py
  6. At least one author email is present in pyproject.toml
  7. frappe-dependencies keys (minus frappe) match required_apps in hooks.py
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from clone_utils import clone_app
from validator import Validator


def parse_github_slug(repo_url: str) -> tuple[str, str]:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", repo_url)
    if not match:
        raise ValueError(f"Cannot parse GitHub URL: {repo_url}")
    return match.group(1), match.group(2)


def github_api_get(path: str) -> dict:
    url = f"https://api.github.com{path}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def find_hooks_file(clone_dir: Path) -> Path | None:
    hooks_files = list(clone_dir.glob("*/hooks.py"))
    return hooks_files[0] if hooks_files else None


def parse_required_apps(hooks_file: Path) -> set[str]:
    tree = ast.parse(hooks_file.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "required_apps":
                if isinstance(node.value, ast.List):
                    return {elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)}
    return set()


class AppValidator(Validator):
    name = "app quality"

    def __init__(self, repo_url: str, clone_dir: Path):
        super().__init__()
        self.repo_url = repo_url
        self.clone_dir = clone_dir

    def validate(self) -> None:
        self._check_github()
        self._check_cloned_app()

    def _check_github(self) -> None:
        try:
            owner, repo = parse_github_slug(self.repo_url)
            data = github_api_get(f"/repos/{owner}/{repo}")
        except Exception as exc:
            self.fail(f"GitHub API error: {exc}")
            return
        if data.get("private"):
            self.fail("Repository must be public")
        if not data.get("description", "").strip():
            self.fail("Repository must have a description")

    def _check_cloned_app(self) -> None:
        pyproject_path = self.clone_dir / "pyproject.toml"
        if not pyproject_path.exists():
            self.fail("Missing pyproject.toml at repository root")
            return
        pyproject = tomllib.loads(pyproject_path.read_text())
        self._check_frappe_dependency(pyproject)
        self._check_dynamic_versioning(pyproject)
        self._check_author_email(pyproject)
        self._check_required_apps(pyproject)

    def _check_frappe_dependency(self, pyproject: dict) -> None:
        deps = pyproject.get("tool", {}).get("bench", {}).get("frappe-dependencies", {})
        if "frappe" not in deps:
            self.fail("pyproject.toml must declare frappe in [tool.bench.frappe-dependencies]")

    def _check_dynamic_versioning(self, pyproject: dict) -> None:
        dynamic = pyproject.get("project", {}).get("dynamic", [])
        if "version" not in dynamic:
            self.fail('pyproject.toml must use dynamic versioning: dynamic = ["version"]')
            return
        app_name = pyproject.get("project", {}).get("name", "").replace("-", "_")
        init_file = self.clone_dir / app_name / "__init__.py"
        if not init_file.exists() or "__version__" not in init_file.read_text():
            self.fail(f"{app_name}/__init__.py must declare __version__")

    def _check_author_email(self, pyproject: dict) -> None:
        authors = pyproject.get("project", {}).get("authors", [])
        if not any(a.get("email") for a in authors):
            self.fail("pyproject.toml must include an author email in [project.authors]")

    def _check_required_apps(self, pyproject: dict) -> None:
        frappe_deps = set(
            pyproject.get("tool", {}).get("bench", {}).get("frappe-dependencies", {}).keys()
        )
        non_frappe_deps = frappe_deps - {"frappe"}
        hooks_file = find_hooks_file(self.clone_dir)
        if hooks_file is None:
            self.fail("Could not find hooks.py in repository")
            return
        try:
            required_apps = parse_required_apps(hooks_file)
        except (SyntaxError, ValueError):
            self.fail("Error occured during the parsing of the hooks.py file, please check the syntax.")
            return

        if non_frappe_deps != required_apps:
            self.fail(
                f"frappe-dependencies (excluding frappe) {sorted(non_frappe_deps)} "
                f"must match required_apps {sorted(required_apps)} in hooks.py"
            )


def main() -> None:
    if len(sys.argv) != 4:
        print("Usage: run_app_validations.py <repo-url> <target> <target_type>", file=sys.stderr)
        sys.exit(1)

    repo_url, target, target_type = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"\n=== Validating {repo_url}@{target} ({target_type}) ===", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "app"
        try:
            clone_app(repo_url, target, target_type, clone_dir)
        except RuntimeError as exc:
            print(f"  FAIL: {exc}")
            sys.exit(1)
        passed = AppValidator(repo_url, clone_dir).run()

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
