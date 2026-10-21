"""Decide which backup runs to prune. Pure logic, no I/O."""

from datetime import date, datetime

from pilot.config import SCHEME_FIFO, BackupConfig

_TS_FORMAT = "%Y%m%d_%H%M%S"


class BackupRetentionPolicy:
    """Select backup timestamps to delete under FIFO or GFS retention."""

    def __init__(self, config: BackupConfig) -> None:
        self.config = config

    def select_deletions(self, timestamps: list[str], today: date | None = None) -> list[str]:
        runs = self._parse(timestamps)
        if len(runs) <= 1:
            return []
        if self.config.scheme == SCHEME_FIFO:
            keep = self._keep_fifo(runs)
        elif self.config.calendar_rotation:
            keep = self._keep_calendar_gfs(runs, today or date.today())
        else:
            keep = self._keep_gfs(runs)
        keep.add(runs[0][1])  # newest run is never pruned
        return [ts for _, ts in runs if ts not in keep]

    def _keep_fifo(self, runs: list[tuple[datetime, str]]) -> set[str]:
        return {ts for _, ts in runs[: self.config.keep_last]}

    def _keep_calendar_gfs(self, runs: list[tuple[datetime, str]], today: date) -> set[str]:
        """Keep every run on an anchor day while its calendar window is open."""
        keep = set()
        for when, timestamp in runs:
            age = (today - when.date()).days
            tiers = (
                (7, True),
                (28, when.weekday() == 6),
                (366, when.day == 1),
                (3653, when.month == 1 and when.day == 1),
            )
            if any(anchor and age <= window for window, anchor in tiers):
                keep.add(timestamp)
        return keep

    def _keep_gfs(self, runs: list[tuple[datetime, str]]) -> set[str]:
        config = self.config
        keep: set[str] = set()
        keep |= self._keep_tier(runs, config.keep_daily, lambda d: (d.year, d.month, d.day))
        keep |= self._keep_tier(runs, config.keep_weekly, lambda d: d.isocalendar()[:2])
        keep |= self._keep_tier(runs, config.keep_monthly, lambda d: (d.year, d.month))
        keep |= self._keep_tier(runs, config.keep_yearly, lambda d: d.year)
        return keep

    @staticmethod
    def _keep_tier(runs: list[tuple[datetime, str]], limit: int, period) -> set[str]:
        """Keep the latest run in each of the most recent ``limit`` periods."""
        if limit <= 0:
            return set()
        latest_per_period: dict = {}
        for when, ts in runs:  # runs are newest-first, so first seen wins
            latest_per_period.setdefault(period(when), ts)
        recent_periods = sorted(latest_per_period, reverse=True)[:limit]
        return {latest_per_period[p] for p in recent_periods}

    @staticmethod
    def _parse(timestamps: list[str]) -> list[tuple[datetime, str]]:
        """Return parseable timestamps newest first."""
        runs = []
        for ts in timestamps:
            try:
                runs.append((datetime.strptime(ts, _TS_FORMAT), ts))
            except ValueError:
                continue
        return sorted(runs, key=lambda run: run[0], reverse=True)
