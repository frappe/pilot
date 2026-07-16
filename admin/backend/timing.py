"""Polling and streaming intervals used across the Admin backend, gathered
here so every timing knob is visible and tunable in one place."""

MAX_STREAM_LINES = 5000  # cap on lines a single log tail-stream connection emits

WATCHDOG_MAX_POLL_SECONDS = 30.0  # watchdog: longest single sleep between activity checks
