from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class TimelinePoint:
    """One timestamped event, already normalized to epoch milliseconds by the provider."""

    time_ms: int
    category: str
    duration: int | float


def build_timeline(
    points: list[TimelinePoint],
    top: int,
    bucket_seconds: int,
    by: str,
    start_ms: int,
    end_ms: int,
) -> dict:
    """The top categories bucketed over time."""
    categories = [name for name, _ in _rank(points, top, by)]
    bucket_ms = bucket_seconds * 1000
    max_bucket = _max_bucket(end_ms, bucket_ms)
    buckets: dict[int, dict[str, float]] = {}
    durations_by_bucket: dict[tuple[int, str], list[int | float]] = {}
    for point in points:
        if point.category not in categories:
            continue
        bucket = min(point.time_ms // bucket_ms * bucket_ms, max_bucket)
        row = buckets.setdefault(bucket, {})
        if by == "count":
            row[point.category] = row.get(point.category, 0) + 1
        elif by == "duration":
            row[point.category] = max(row.get(point.category, 0), round(point.duration / 1_000_000, 3))
        else:
            durations_by_bucket.setdefault((bucket, point.category), []).append(point.duration)
    for (bucket, category), durations in durations_by_bucket.items():
        buckets.setdefault(bucket, {})[category] = _average_seconds(durations)
    _fill_bucket_range(buckets, start_ms, end_ms, bucket_ms, dict)
    return {
        "categories": categories,
        "points": [{"time": time, **values} for time, values in sorted(buckets.items())],
    }


def build_total_timeline(
    points: list[TimelinePoint],
    bucket_seconds: int,
    series_name: str,
    start_ms: int,
    end_ms: int,
) -> dict:
    """The total count bucketed over time."""
    bucket_ms = bucket_seconds * 1000
    max_bucket = _max_bucket(end_ms, bucket_ms)
    buckets: dict[int, int] = {}
    for point in points:
        bucket = min(point.time_ms // bucket_ms * bucket_ms, max_bucket)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    _fill_bucket_range(buckets, start_ms, end_ms, bucket_ms, int)
    return {
        "categories": [series_name],
        "points": [{"time": time, series_name: count} for time, count in sorted(buckets.items())],
    }


def _fill_bucket_range(buckets: dict, start_ms: int, end_ms: int, bucket_ms: int, default) -> None:
    """Fill the empty buckets in the range."""
    first_bucket = start_ms // bucket_ms * bucket_ms
    for bucket in range(first_bucket, end_ms // bucket_ms * bucket_ms, bucket_ms):
        buckets.setdefault(bucket, default())


def _max_bucket(end_ms: int, bucket_ms: int) -> int:
    """The last complete bucket."""
    return end_ms // bucket_ms * bucket_ms - bucket_ms


def _average_seconds(durations: list[int | float]) -> float:
    """The average duration in seconds."""
    return round(sum(durations) / len(durations) / 1_000_000, 3)


def _rank(points: list[TimelinePoint], top: int, by: str) -> list[tuple[str, int | float]]:
    """The top categories by count or duration."""
    if by == "count":
        counts = Counter(point.category for point in points)
        return [(name, count) for name, count in counts.most_common(top)]
    if by == "duration":
        slowest: dict[str, int | float] = {}
        for point in points:
            slowest[point.category] = max(slowest.get(point.category, 0), point.duration)
        return sorted(slowest.items(), key=lambda item: item[1], reverse=True)[:top]
    durations_by_category: dict[str, list[int | float]] = {}
    for point in points:
        durations_by_category.setdefault(point.category, []).append(point.duration)
    averages = [(name, sum(d) / len(d)) for name, d in durations_by_category.items()]
    return sorted(averages, key=lambda item: item[1], reverse=True)[:top]
