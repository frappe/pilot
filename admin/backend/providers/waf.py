from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_SECONDS = {"30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "1w": 604800}
_MAX_BUCKETS = 48
_TOP_LIMIT = 10
# CRS scoring/correlation rules (949xxx inbound anomaly, 980xxx reporting) are
# bookkeeping, not attacks — keep them out of the "top rules" breakdown.
_SUMMARY_RULE_PREFIXES = ("949", "980")
_INBOUND_ANOMALY_RULE = "949110"
_TIME_FORMATS = ("%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")


class WafProvider:
    """Aggregates the ModSecurity JSON audit log into WAF analytics for a window.

    The audit log is JSON Lines: one transaction per line, written only for
    requests that tripped a rule (SecAuditEngine RelevantOnly). Field names vary
    slightly across ModSecurity builds, so extraction is deliberately lenient.
    """

    def __init__(self, bench_root: Path, window: str) -> None:
        self._log_path = bench_root / "logs" / "modsec_audit.log"
        self._bench_root = bench_root
        self._window = window if window in WINDOW_SECONDS else "1h"
        self._cutoff = datetime.now() - timedelta(seconds=WINDOW_SECONDS[self._window])

    def get_analytics(self) -> dict:
        flagged = blocked = would_block = 0
        rules: Counter = Counter()
        ips: Counter = Counter()
        buckets: Counter = Counter()
        bucket_blocked: Counter = Counter()
        bucket_size = max(60, WINDOW_SECONDS[self._window] // _MAX_BUCKETS)

        for record in self._iter_records_reversed(self._log_path):
            transaction = record.get("transaction", record)
            when = self._parse_time(self._first(transaction, "time_stamp", "timestamp", "time"))
            if when is None:
                continue  # can't place it in the window — skip, don't inflate totals
            if when < self._cutoff:
                break  # older than the window; the rest is older still
            messages = self._first(transaction, "messages") or []
            if not messages:
                continue
            flagged += 1
            is_blocked = self._status_code(transaction) == 403
            crossed = any(self._rule_id(m) == _INBOUND_ANOMALY_RULE for m in messages)
            blocked += is_blocked
            would_block += crossed
            ip = self._first(transaction, "client_ip", "client ip")
            if ip:
                ips[ip] += 1
            for message in messages:
                rule_id = self._rule_id(message)
                if rule_id and not rule_id.startswith(_SUMMARY_RULE_PREFIXES):
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
                for (rule_id, label), count in rules.most_common(_TOP_LIMIT)
            ],
            "top_ips": [{"ip": ip, "count": count} for ip, count in ips.most_common(_TOP_LIMIT)],
            "series": [
                {"t": bucket * 1000, "flagged": count, "blocked": bucket_blocked[bucket]}
                for bucket, count in sorted(buckets.items())
            ],
        }

    def _mode(self) -> str:
        from pilot.config.toml_store import BenchTomlStore

        try:
            return BenchTomlStore.for_bench(self._bench_root).read().waf.mode
        except Exception:
            return "DetectionOnly"

    @staticmethod
    def _first(data: dict, *keys: str):
        for key in keys:
            if isinstance(data, dict) and data.get(key) not in (None, ""):
                return data[key]
        return None

    @classmethod
    def _rule_id(cls, message: dict) -> str:
        details = cls._first(message, "details", "data") or {}
        return str(cls._first(details, "ruleId", "id") or "")

    @classmethod
    def _rule_label(cls, message: dict) -> str:
        details = cls._first(message, "details", "data") or {}
        return str(cls._first(details, "msg") or cls._first(message, "message") or "")

    @classmethod
    def _status_code(cls, transaction: dict) -> int | None:
        response = cls._first(transaction, "response") or {}
        code = cls._first(response, "http_code", "http_status", "status")
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_time(value) -> datetime | None:
        if not value:
            return None
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
        for fmt in _TIME_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _iter_records_reversed(path: Path, block_size: int = 65536):
        """Yield JSON records newest-first, reading the file from the end so a short
        window never touches the whole log. Malformed lines are skipped."""
        if not path.exists():
            return
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            remainder = b""
            while position > 0:
                size = min(block_size, position)
                position -= size
                handle.seek(position)
                lines = (handle.read(size) + remainder).split(b"\n")
                remainder = lines[0]
                for line in reversed(lines[1:]):
                    record = _safe_json(line)
                    if record is not None:
                        yield record
            record = _safe_json(remainder)
            if record is not None:
                yield record


def _safe_json(line: bytes):
    if not line.strip():
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None
