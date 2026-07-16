"""Polling and grace-period intervals used by the task engine, gathered here
so every timing knob is visible and tunable in one place."""

TASK_POLL_SECONDS = 0.5  # task_reader: how often to check for new task output
PROCESS_EXIT_POLL_SECONDS = 0.05  # task_process: how often to check if a task process exited
CAPTURE_POLL_SECONDS = 0.01  # process_identity: how often to poll for a child's PID
CAPTURE_TIMEOUT_SECONDS = 1.0  # process_identity: longest time to wait for a child's PID
CANCEL_GRACE_SECONDS = 3.0  # task_process: default wait after SIGTERM before force-killing
