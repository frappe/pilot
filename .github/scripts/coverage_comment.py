"""Build a short Markdown coverage comment from a Cobertura XML report."""

import argparse
import os
import subprocess
import xml.etree.ElementTree as ET

MARKER = "<!-- pilot-coverage-comment -->"


def parse_report(path):
    """Return ({filename: (covered, valid)}, total_covered, total_valid)."""
    files = {}
    total_covered = 0
    total_valid = 0
    for cls in ET.parse(path).getroot().iter("class"):
        name = (cls.get("filename") or "").strip()
        if not name:
            continue
        covered = 0
        valid = 0
        for line in cls.iter("line"):
            valid += 1
            if line.get("hits", "0") != "0":
                covered += 1
        previous = files.get(name, (0, 0))
        files[name] = (previous[0] + covered, previous[1] + valid)
        total_covered += covered
        total_valid += valid
    return files, total_covered, total_valid


def get_changed_files(base_ref):
    """List changed *.py files vs the base branch; [] when git is unavailable."""
    subprocess.run(
        ["git", "fetch", "--no-tags", "--depth", "1", "origin", base_ref],
        capture_output=True,
        text=True,
        check=True,
    )
    # Two-dot endpoint diff: three-dot needs a merge base, which a
    # depth-1 checkout plus a depth-1 fetch does not provide.
    completed = subprocess.run(
        ["git", "diff", "--name-only", "FETCH_HEAD..HEAD", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def build_markdown(files, covered, valid, changed, run_url):
    overall = covered / valid * 100 if valid else 0
    lines = [MARKER, "", "## Code Coverage", ""]
    rows = [(name, *files[name]) for name in changed if name in files]
    if rows:
        lines.append("| File | Covered | Total | Coverage |")
        lines.append("| --- | ---: | ---: | ---: |")
        for name, file_covered, file_valid in sorted(rows):
            percent = file_covered / file_valid * 100 if file_valid else 0
            lines.append(f"| `{name}` | {file_covered} | {file_valid} | {percent:.1f}% |")
        lines.append("")
    else:
        lines.append("No changed Python files with coverage in this PR.")
        lines.append("")
    summary = f"**{overall:.1f}%** overall ({covered}/{valid} statements)"
    if run_url:
        summary += f" · [workflow run]({run_url})"
    lines.append(summary)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Build a Markdown coverage comment from Cobertura XML.")
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--base-ref", default=os.environ.get("GITHUB_BASE_REF", ""))
    args = parser.parse_args()
    files, covered, valid = parse_report(args.coverage)
    try:
        changed = get_changed_files(args.base_ref) if args.base_ref else []
    except subprocess.CalledProcessError:
        changed = []
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(build_markdown(files, covered, valid, changed, args.run_url))
    print(f"Wrote coverage comment: {covered}/{valid} statements across {len(files)} files")


if __name__ == "__main__":
    main()
