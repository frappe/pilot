from dataclasses import dataclass

# Retention schemes for pruning old backups.
SCHEME_FIFO = "fifo"
SCHEME_GFS = "gfs"
VALID_SCHEMES = (SCHEME_FIFO, SCHEME_GFS)


@dataclass
class BackupConfig:
    scheme: str = SCHEME_GFS  # "fifo" | "gfs"
    keep_last: int = 7  # FIFO: newest N runs kept
    keep_daily: int = 7  # GFS tiers below
    keep_weekly: int = 4
    keep_monthly: int = 6
    keep_yearly: int = 1

    @property
    def counts(self) -> dict[str, int]:
        return {
            "keep_last": self.keep_last,
            "keep_daily": self.keep_daily,
            "keep_weekly": self.keep_weekly,
            "keep_monthly": self.keep_monthly,
            "keep_yearly": self.keep_yearly,
        }
