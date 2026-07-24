from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

DEFAULT_TOP = 5
DEFAULT_BUCKET_SECONDS = 600


@dataclass
class TimelinePoint:
    """One timestamped event, already normalized to epoch milliseconds by the provider."""

    time_ms: int
    category: str
    duration: int | float


def build_timeline(
    points: list[TimelinePoint],
    top: int = DEFAULT_TOP,
    bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
    by: str = "count",
) -> dict:
    """Bucket points into a per-category time series for the top N categories,
    ranked by request count (by="count") or worst duration (by="duration")."""
    categories = [name for name, _ in _rank(points, top, by)]
    bucket_ms = bucket_seconds * 1000
    buckets: dict[int, dict[str, float]] = {}
    for point in points:
        if point.category not in categories:
            continue
        row = buckets.setdefault(point.time_ms // bucket_ms * bucket_ms, {})
        if by == "count":
            row[point.category] = row.get(point.category, 0) + 1
        else:
            # duration is microseconds; keep millisecond precision within a seconds unit.
            row[point.category] = max(row.get(point.category, 0), round(point.duration / 1_000_000, 3))
    return {
        "categories": categories,
        "points": [{"time": time, **values} for time, values in sorted(buckets.items())],
    }


def _rank(points: list[TimelinePoint], top: int, by: str) -> list[tuple[str, int | float]]:
    if by == "count":
        counts = Counter(point.category for point in points)
        return [(name, count) for name, count in counts.most_common(top)]
    slowest: dict[str, int | float] = {}
    for point in points:
        slowest[point.category] = max(slowest.get(point.category, 0), point.duration)
    return sorted(slowest.items(), key=lambda item: item[1], reverse=True)[:top]
