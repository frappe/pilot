import argparse
import ast
import re
import sys
from pathlib import Path


class CodeShapeReport:
    """Counts functions without a docstring and module constants that start with an underscore."""

    private_constant = re.compile(r"^_[A-Z][A-Z0-9_]*$")

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def files(self) -> list[Path]:
        """Every Python file under the root, without vendored code."""
        return sorted(path for path in self.root.rglob("*.py") if "_vendor" not in path.parts)

    def get_missing_docstrings(self, path: Path) -> list[str]:
        """`file:line name` for each function or method without a docstring. Dunder methods are
        covered by their class docstring."""
        tree = ast.parse(path.read_text())
        return [
            f"{path}:{node.lineno} {node.name}"
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not (node.name.startswith("__") and node.name.endswith("__"))
            and ast.get_docstring(node) is None
        ]

    def get_private_constants(self, path: Path) -> list[str]:
        """`file:line name` for each module-level assignment to an `_UPPER_CASE` name."""
        tree = ast.parse(path.read_text())
        found = []
        for node in tree.body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and self.private_constant.match(target.id):
                    found.append(f"{path}:{node.lineno} {target.id}")
        return found

    def print_report(self, verbose: bool) -> int:
        """Print both counts, and each finding when verbose; return the total."""
        missing = [item for path in self.files for item in self.get_missing_docstrings(path)]
        constants = [item for path in self.files for item in self.get_private_constants(path)]
        if verbose:
            print("\n".join(missing + constants))
        print(f"{self.root}: {len(missing)} without docstring, {len(constants)} _CONSTANTS")
        return len(missing) + len(constants)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=CodeShapeReport.__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--verbose", action="store_true", help="list each finding")
    parser.add_argument("--strict", action="store_true", help="exit 1 when anything is found")
    options = parser.parse_args()
    total = sum(CodeShapeReport(root).print_report(options.verbose) for root in options.roots)
    sys.exit(1 if options.strict and total else 0)
