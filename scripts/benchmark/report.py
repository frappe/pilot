"""Markdown and terminal reports for benchmark results."""

from __future__ import annotations

from benchmark.harness import CaseResult, compare_to_baseline


def _format_ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f}s"
    return f"{value:.2f}ms"


def _results_table(results: list[CaseResult]) -> str:
    rows = []
    for result in results:
        for measurement in result.measurements:
            rows.append(
                (
                    result.group,
                    result.name,
                    measurement.label,
                    measurement.min_ms,
                    measurement.median_ms,
                    measurement.mean_ms,
                    measurement.p90_ms,
                    measurement.cpu_median_ms,
                    len(measurement.samples_ms),
                )
            )
    rows.sort(key=lambda row: row[4], reverse=True)
    lines = [
        "| Group | Case | Step | Min | Median | Mean | P90 | CPU median | Iterations |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group, name, label, minimum, median, mean, p90, cpu, iterations in rows:
        step = "" if label == name else label
        lines.append(
            f"| {group} | {name} | {step} | {_format_ms(minimum)} | {_format_ms(median)} | "
            f"{_format_ms(mean)} | {_format_ms(p90)} | {_format_ms(cpu)} | {iterations} |"
        )
    return "\n".join(lines)


def _errors_table(results: list[CaseResult]) -> str:
    errors = [result for result in results if result.error]
    if not errors:
        return "No case errors."
    lines = ["| Case | Error |", "|---|---|"]
    for result in errors:
        lines.append(f"| {result.name} | {result.error} |")
    return "\n".join(lines)


def _comparison_table(rows: list[dict]) -> str:
    if not rows:
        return "No overlapping cases with the baseline."
    lines = [
        "| Case | Step | Baseline | Current | Change |",
        "|---|---|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda item: abs(item["change_percent"]), reverse=True):
        change = row["change_percent"]
        marker = "🔴" if change > 10 else "🟢" if change < -10 else "⚪"
        lines.append(
            f"| {row['case']} | {row['step']} | {_format_ms(row['baseline_ms'])} | "
            f"{_format_ms(row['current_ms'])} | {marker} {change:+.1f}% |"
        )
    return "\n".join(lines)


def render_markdown(
    results: list[CaseResult],
    meta: dict,
    baseline: dict | None = None,
) -> str:
    comparison = compare_to_baseline(results, baseline) if baseline else []
    lines = [
        "# Pilot performance benchmark",
        "",
        f"Generated: {meta['timestamp']}",
        "",
        f"Environment: Python {meta['python_version']} on {meta['platform']}",
        "",
        f"Git: `{meta['git_sha']}`",
        "",
        (
            "Fixture: "
            f"{meta['fixture']['apps']} apps, "
            f"{meta['fixture']['sites']} sites, "
            f"{meta['fixture']['tasks']} tasks, "
            f"{meta['fixture']['log_lines']} log lines"
        ),
        "",
        "## Slowest measurements",
        "",
        _results_table(results),
        "",
        "## Errors",
        "",
        _errors_table(results),
        "",
        "## Comparison to baseline",
        "",
        _comparison_table(comparison),
        "",
        "## Profiles",
        "",
    ]
    profiles = [result for result in results if result.profile_path]
    if profiles:
        for result in profiles:
            lines.append(f"- `{result.name}`: `{result.profile_path}`")
    else:
        lines.append("No profiles were collected in this run.")
    lines.append("")
    return "\n".join(lines)


def render_terminal_summary(results: list[CaseResult], baseline: dict | None = None) -> str:
    rows = []
    for result in results:
        for measurement in result.measurements:
            change = ""
            if baseline:
                key = f"{result.name}:{measurement.label}"
                if baseline.get(key):
                    delta = (measurement.median_ms - baseline[key]) / baseline[key] * 100
                    change = f" ({delta:+.1f}%)"
            error = " ERROR" if result.error else ""
            label = "" if measurement.label == result.name else f" / {measurement.label}"
            rows.append(
                (
                    measurement.median_ms,
                    f"{result.name}{label}{change}: {_format_ms(measurement.min_ms)} min, "
                    f"{_format_ms(measurement.median_ms)} median, "
                    f"{_format_ms(measurement.p90_ms)} p90, "
                    f"{_format_ms(measurement.cpu_median_ms)} cpu{error}",
                ),
            )
    rows.sort(key=lambda row: row[0], reverse=True)
    return "\n".join(row[1] for row in rows)


__all__ = ["render_markdown", "render_terminal_summary"]
