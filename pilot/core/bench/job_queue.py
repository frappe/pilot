from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Runs on the bench's Python, so it uses the redis and rq versions of the bench.
_PROGRAM = '''\
import json
import os
import socket
import sys


def is_alive(worker) -> bool:
    """A worker of another host, or one without a PID, counts as alive."""
    if worker.hostname != socket.gethostname() or not worker.pid:
        return True
    try:
        os.kill(worker.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def get_active_workers(workers: list) -> list[str]:
    """The workers that can still take a job.

    The job threads of a lite runner share one PID, and a no-fork worker sends no heartbeat
    during a job. So a worker left behind by a re-exec looks like a busy thread, and counts."""
    return [worker.name for worker in workers if worker.get_state() != "suspended" and is_alive(worker)]


def main(action: str, url: str) -> None:
    from redis import Redis
    from rq import Worker
    from rq.suspension import is_suspended, resume, suspend

    connection = Redis.from_url(url)
    if action == "suspend":
        suspend(connection)
    elif action == "resume":
        resume(connection)
    workers = Worker.all(connection=connection)
    state = {"is_suspended": bool(is_suspended(connection)), "active_workers": get_active_workers(workers)}
    print(json.dumps(state))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
'''


@dataclass(frozen=True)
class JobQueueState:
    is_suspended: bool
    active_workers: list[str]

    @property
    def is_held(self) -> bool:
        """Whether no worker can take a job."""
        return self.is_suspended and not self.active_workers


class JobQueue:
    """The RQ queue of the bench. While it is suspended, new jobs wait in Redis, also across worker restarts.

    A worker reads the suspend flag only between jobs, not while it waits for one.
    So restart the workers after `suspend()`, and check `state.is_held`."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def url(self) -> str:
        return f"redis://localhost:{self.bench.config.redis.queue_port}"

    @property
    def state(self) -> JobQueueState:
        return self._run("state")

    def suspend(self) -> JobQueueState:
        return self._run("suspend")

    def resume(self) -> JobQueueState:
        return self._run("resume")

    def _run(self, action: str) -> JobQueueState:
        argv = [str(self.bench.python), "-I", "-c", _PROGRAM, action, self.url]
        result = run_command(argv, cwd=self.bench.path, timeout=60)
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as error:
            raise BenchError(
                f"Cannot read the job queue state of {self.bench.config.name}: {error}"
            ) from error
        return JobQueueState(bool(data["is_suspended"]), list(data["active_workers"]))
