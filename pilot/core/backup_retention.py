"""Decide which backup runs to prune. Pure logic, no I/O."""

from datetime import datetime

from pilot.config.backup_config import SCHEME_FIFO, BackupConfig

_TS_FORMAT = "%Y%m%d_%H%M%S"


class BackupRetentionPolicy:
    """Given backup run timestamps (`YYYYMMDD_HHMMSS`), decide which to delete.

    FIFO keeps the newest ``keep_last`` runs. GFS keeps the latest run in each of
    the most recent ``keep_daily`` days, ``keep_weekly`` ISO weeks, ``keep_monthly``
    months and ``keep_yearly`` years; a run survives if any tier selects it. Either
    way the single newest run is always kept.
    """

    def __init__(self, config: BackupConfig) -> None:
        self.config = config

    def select_deletions(self, timestamps: list[str]) -> list[str]:
        runs = self._parse(timestamps)
        if len(runs) <= 1:
            return []
        keep = self._keep_fifo(runs) if self.config.scheme == SCHEME_FIFO else self._keep_gfs(runs)
        keep.add(runs[0][1])  # newest run is never pruned
        return [ts for _, ts in runs if ts not in keep]

    def _keep_fifo(self, runs: list[tuple[datetime, str]]) -> set[str]:
        return {ts for _, ts in runs[: self.config.keep_last]}

    def _keep_gfs(self, runs: list[tuple[datetime, str]]) -> set[str]:
        cfg = self.config
        keep: set[str] = set()
        keep |= self._keep_tier(runs, cfg.keep_daily, lambda d: (d.year, d.month, d.day))
        keep |= self._keep_tier(runs, cfg.keep_weekly, lambda d: d.isocalendar()[:2])
        keep |= self._keep_tier(runs, cfg.keep_monthly, lambda d: (d.year, d.month))
        keep |= self._keep_tier(runs, cfg.keep_yearly, lambda d: d.year)
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
        """Parseable timestamps, newest first. Unparseable ones are dropped so
        they are never selected for deletion."""
        runs = []
        for ts in timestamps:
            try:
                runs.append((datetime.strptime(ts, _TS_FORMAT), ts))
            except ValueError:
                continue
        return sorted(runs, key=lambda run: run[0], reverse=True)
