from __future__ import annotations

import typing
from pathlib import Path

from pilot.core.app.validator.base import get_bench_python, module_path
from pilot.core.app.validator.utils.bench_runner import run_in_bench
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App

_DICT_HOOKS = frozenset(
    [
        "additional_timeline_content",
        "base_template_map",
        "doc_events",
        "doctype_js",
        "extend_doctype_class",
        "extend_website_page_controller_context",
        "has_permission",
        "jinja",
        "override_doctype_class",
        "override_whitelisted_methods",
        "page_js",
        "permission_query_conditions",
        "role_home_page",
        "scheduler_events",
        "standard_queries",
        "webform_include_css",
        "webform_include_js",
        "website_context",
    ]
)

_PATH_HOOKS = frozenset(
    [
        "additional_timeline_content",
        "after_build",
        "after_install",
        "after_migrate",
        "after_sync",
        "after_uninstall",
        "auth_hooks",
        "before_install",
        "before_migrate",
        "before_tests",
        "before_uninstall",
        "before_write_file",
        "boot_session",
        "clear_cache",
        "delete_file_data_content",
        "doc_events",
        "extend_bootinfo",
        "extend_doctype_class",
        "extend_website_page_controller_context",
        "get_sender_details",
        "get_web_pages_with_dynamic_routes",
        "get_website_user_home_page",
        "has_permission",
        "jinja",
        "notification_config",
        "on_login",
        "on_logout",
        "on_session_creation",
        "override_doctype_class",
        "override_email_send",
        "override_whitelisted_methods",
        "permission_query_conditions",
        "scheduler_events",
        "send_sms",
        "send_token_via_sms",
        "standard_queries",
        "update_website_context",
        "website_clear_cache",
        "website_path_resolver",
        "write_file",
    ]
)

_HOOKS_AST_SCRIPT = """
import ast, json, sys

req = json.load(sys.stdin)
path = req["path"]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        tree = ast.parse(f.read(), filename=path)
except SyntaxError as exc:
    print(json.dumps({"syntax_error": f"line {exc.lineno}: {exc.msg}"}))
    sys.exit(0)
except (OSError, UnicodeDecodeError):
    print(json.dumps({"dict_errors": [], "path_hooks": []}))
    sys.exit(0)

dict_hooks = set(req.get("dict_hooks", []))
path_hooks = set(req.get("path_hooks", []))

def string_values(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(node.value, node.lineno)]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [found for el in node.elts for found in string_values(el)]
    if isinstance(node, ast.Dict):
        return [found for val in node.values if val for found in string_values(val)]
    return []

not_a_dict = (ast.List, ast.Tuple, ast.Set, ast.Constant)
dict_errors = []
found_paths = []

for node in tree.body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                name = target.id
                val = node.value
                if name in dict_hooks and isinstance(val, not_a_dict):
                    dict_errors.append(f"line {val.lineno}: {name} must be a dict")
                elif name in path_hooks:
                    for p, lineno in string_values(val):
                        found_paths.append([name, p, lineno])

print(json.dumps({"dict_errors": dict_errors, "path_hooks": found_paths}))
"""

_SYMBOLS_AST_SCRIPT = """
import ast, json, sys

req = json.load(sys.stdin)
path = req["path"]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        tree = ast.parse(f.read(), filename=path)
except SyntaxError as exc:
    print(json.dumps({"syntax_error": f"line {exc.lineno}: {exc.msg}"}))
    sys.exit(0)
except (OSError, UnicodeDecodeError):
    print(json.dumps({"symbols": [], "wildcard": False}))
    sys.exit(0)

def reachable_statements(body):
    stmts = []
    for node in body:
        stmts.append(node)
        if isinstance(node, ast.If):
            stmts += reachable_statements(node.body + node.orelse)
        elif isinstance(node, ast.Try):
            handled = [s for h in node.handlers for s in h.body]
            stmts += reachable_statements(node.body + node.orelse + node.finalbody + handled)
    return stmts

symbols = set()
wildcard = False
for node in reachable_statements(tree.body):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        symbols.add(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            if alias.name == "*":
                wildcard = True
                break
            symbols.add(alias.asname or alias.name.split(".", 1)[0])
        if wildcard:
            break
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                symbols.add(t.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        symbols.add(node.target.id)

print(json.dumps({"wildcard": wildcard, "symbols": list(symbols)}))
"""


@typing.final
class _HooksData(typing.NamedTuple):
    dict_errors: list[str]
    path_hooks: list[tuple[str, str, int]]


class HooksCheck:
    """Verify hooks.py shapes and that its dotted paths point at real code.

    Only documented hooks are inspected; app-specific hook names are left alone.
    SyntaxCheck guarantees hooks.py parses first.
    """

    def run(self, app: "App") -> None:
        hooks_path = module_path(app) / "hooks.py"
        if not hooks_path.is_file():
            return  # RepoStructureCheck owns this when it runs; updates skip it

        problems = self._validate_hooks(app, hooks_path)
        if problems:
            raise AppValidationError(
                f"'{app.config.name}' has invalid hooks in {app.module_name}/hooks.py:\n"
                + "\n".join(f"  {problem}" for problem in problems)
                + "\nPoint each path at code that exists (or drop the hook). Hook shapes: "
                "https://docs.frappe.io/framework/user/en/python-api/hooks"
            )

    @classmethod
    def _validate_hooks(cls, app: "App", hooks_path: Path) -> list[str]:
        data = cls._extract_hooks_data(app, hooks_path)
        problems = list(data.dict_errors)
        for name, path, lineno in data.path_hooks:
            error = _path_error(app, path)
            if error:
                problems.append(f"line {lineno}: {name} -> {path}: {error}")
        return problems

    @classmethod
    def _extract_hooks_data(cls, app: "App", hooks_path: Path) -> _HooksData:
        bench_python = get_bench_python(app)
        payload = {
            "path": str(hooks_path),
            "dict_hooks": list(_DICT_HOOKS),
            "path_hooks": list(_PATH_HOOKS),
        }
        data = run_in_bench(bench_python, _HOOKS_AST_SCRIPT, payload)
        if "syntax_error" in data:
            raise AppValidationError(
                f"'{app.config.name}' has syntax errors in {app.module_name}/hooks.py: {data['syntax_error']}"
            )
        return _HooksData(
            dict_errors=data.get("dict_errors", []),
            path_hooks=[(item[0], item[1], item[2]) for item in data.get("path_hooks", [])],
        )


def _resolve_package(app: "App", app_module: str) -> Path:
    if app_module == app.module_name:
        return module_path(app)
    return app.bench.apps_path / app_module / app_module


def _path_error(app: "App", dotted: str) -> str | None:
    """Why a hook's dotted path doesn't point at real code, or None if it does.

    `"myapp.setup.after_migrate"` looks for `after_migrate` in `myapp/setup.py`.
    """
    app_module, *rest = dotted.rsplit(":", 1)[-1].split(".")  # jenv-style "alias:path"
    package = _resolve_package(app, app_module)
    if not package.is_dir():
        return None

    module_file, attributes = _find_module(package, rest)
    if module_file is None:
        return f"no module '{dotted}'"
    if not attributes:
        return None  # the path names a module, not something inside one

    return _check_module_attribute(module_file, attributes[0], app)


def _check_module_attribute(module_file: Path, attribute: str, app: "App") -> str | None:
    result = _top_level_symbols(module_file, app)
    if isinstance(result, str):
        return f"cannot parse '{module_file.name}': {result}"
    if result is None or attribute in result:
        return None
    # Only the first attribute is checked, so `some.module.Class.method` stops at `Class`.
    module_name = module_file.parent.name if module_file.stem == "__init__" else module_file.stem
    return f"'{module_name}' has no '{attribute}'"


def _find_module(package: Path, parts: list[str]) -> tuple[Path | None, list[str]]:
    """Split a path's parts into the module file they name and the attributes after it.

    `["setup", "after_migrate"]` -> `(myapp/setup.py, ["after_migrate"])`.
    """
    current = package
    for index, part in enumerate(parts):
        if (current / part).is_dir():
            current = current / part  # a package - keep walking
        elif (current / f"{part}.py").is_file():
            return current / f"{part}.py", parts[index + 1 :]
        else:
            return _package_init(current), parts[index:]
    return _package_init(current), []


def _package_init(package: Path) -> Path | None:
    init = package / "__init__.py"
    return init if init.is_file() else None


def _top_level_symbols(module_file: Path, app: "App" | None = None) -> set[str] | str | None:
    """Names a module defines or imports - everything frappe's get_attr() could find.

    None means a `from x import *` hides them, so nothing can be concluded.
    Returns error string if syntax is unparseable.
    """
    bench_python = get_bench_python(app)
    data = run_in_bench(bench_python, _SYMBOLS_AST_SCRIPT, {"path": str(module_file)})
    if "syntax_error" in data:
        return f"syntax error: {data['syntax_error']}"
    if data.get("wildcard"):
        return None
    return set(data.get("symbols", []))
