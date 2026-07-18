from __future__ import annotations

import re

# Matches the RFC 5424 envelope the wrapper stamps onto output.log:
# <PRI>VERSION TIMESTAMP HOST APP-NAME PROCID MSGID STRUCTURED-DATA MESSAGE
_SYSLOG_RE = re.compile(r"^<\d+>\d+ \S+ \S+ \S+ \S+ \S+ \S+ (.*)$")


def collapse_cr(line: str) -> str:
    if "\r" not in line:
        return line
    parts = line.split("\r")
    return next((part for part in reversed(parts) if part.strip()), "")


def display_line(raw_line: str) -> str:
    stripped = "\r".join(_strip_syslog_envelope(segment) for segment in raw_line.split("\r"))
    return collapse_cr(stripped)


def _strip_syslog_envelope(segment: str) -> str:
    match = _SYSLOG_RE.match(segment)
    return match.group(1) if match else segment
