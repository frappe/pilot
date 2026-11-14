from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from pilot.internal.jsonl_log import JsonLines

WINDOW_SECONDS = {"30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "1w": 604800}
# CRS scoring/correlation rules (949xxx inbound anomaly, 980xxx reporting) are
# bookkeeping, not attacks - keep them out of the "top rules" breakdown.


class WafProvider:
    """Aggregates ModSecurity JSON audit logs for one time window."""

    top_limit = 10

    time_formats = ("%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")

    def __init__(self, bench_root: Path, window: str) -> None:
        self._log_path = bench_root / "logs" / "modsec_audit.log"
        self._bench_root = bench_root
        self._window = window if window in WINDOW_SECONDS else "1h"
        self._cutoff = datetime.now() - timedelta(seconds=WINDOW_SECONDS[self._window])

    def get_analytics(self) -> dict:
        """The WAF event counts, top rules and top IPs."""
        flagged = blocked = would_block = 0
        rules: Counter = Counter()
        ips: Counter = Counter()
        buckets: Counter = Counter()
        bucket_blocked: Counter = Counter()
        bucket_size = max(60, WINDOW_SECONDS[self._window] // 48)

        for record in JsonLines(self._log_path).read_newest_first():
            transaction = record.get("transaction", record)
            when = self._parse_time(self._first(transaction, "time_stamp", "timestamp", "time"))
            if when is None:
                continue  # can't place it in the window - skip, don't inflate totals
            if when < self._cutoff:
                break  # older than the window; the rest is older still
            messages = self._first(transaction, "messages") or []
            if not messages:
                continue
            flagged += 1
            is_blocked = self._status_code(transaction) == 403
            crossed = any(self._rule_id(m) == "949110" for m in messages)
            blocked += is_blocked
            would_block += crossed
            ip = self._first(transaction, "client_ip", "client ip")
            if ip:
                ips[ip] += 1
            for message in messages:
                rule_id = self._rule_id(message)
                if rule_id and not rule_id.startswith(("949", "980")):
                    rules[(rule_id, self._rule_label(message))] += 1
            bucket = int(when.timestamp() // bucket_size * bucket_size)
            buckets[bucket] += 1
            bucket_blocked[bucket] += is_blocked

        return {
            "window": self._window,
            "window_seconds": WINDOW_SECONDS[self._window],
            "now": int(datetime.now().timestamp() * 1000),
            "mode": self._mode(),
            "log_present": self._log_path.exists(),
            "totals": {"flagged": flagged, "blocked": blocked, "would_block": would_block},
            "top_rules": [
                {"id": rule_id, "message": label, "count": count}
                for (rule_id, label), count in rules.most_common(self.top_limit)
            ],
            "top_ips": [{"ip": ip, "count": count} for ip, count in ips.most_common(self.top_limit)],
            "series": [
                {"t": bucket * 1000, "flagged": count, "blocked": bucket_blocked[bucket]}
                for bucket, count in sorted(buckets.items())
            ],
        }

    def _mode(self) -> str:
        """The WAF mode."""
        from pilot.config import BenchConfig

        try:
            return BenchConfig.read(self._bench_root).waf.mode
        except Exception:
            return "DetectionOnly"

    @staticmethod
    def _first(data: dict, *keys: str):
        """The first non-empty value of the keys."""
        for key in keys:
            if isinstance(data, dict) and data.get(key) not in (None, ""):
                return data[key]
        return None

    @classmethod
    def _rule_id(cls, message: dict) -> str:
        """The rule ID of a message."""
        details = cls._first(message, "details", "data") or {}
        return str(cls._first(details, "ruleId", "id") or "")

    @classmethod
    def _rule_label(cls, message: dict) -> str:
        """The rule label of a message."""
        details = cls._first(message, "details", "data") or {}
        return str(cls._first(details, "msg") or cls._first(message, "message") or "")

    @classmethod
    def _status_code(cls, transaction: dict) -> int | None:
        """The response status of a transaction, if any."""
        response = cls._first(transaction, "response") or {}
        code = cls._first(response, "http_code", "http_status", "status")
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_time(value) -> datetime | None:
        """The time of a log value, or None."""
        if not value:
            return None
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
        for fmt in WafProvider.time_formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None
