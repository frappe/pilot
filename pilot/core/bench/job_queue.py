from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Runs on the bench's Python, so it uses the redis and rq versions of the bench.
# A worker of another host, or one with no PID, counts as alive.
_PROGRAM = """
import json, os, socket, sys
from redis import Redis
from rq import Worker
from rq.suspension import is_suspended, resume, suspend

action, url = sys.argv[1], sys.argv[2]
connection = Redis.from_url(url)
if action == "suspend":
    suspend(connection)
elif action == "resume":
    resume(connection)

def is_alive(worker):
    if worker.hostname != socket.gethostname() or not worker.pid:
        return True
    try:
        os.kill(worker.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

workers = Worker.all(connection=connection)
active = [w.name for w in workers if w.get_state() != "suspended" and is_alive(w)]
print(json.dumps({"is_suspended": bool(is_suspended(connection)), "active_workers": active}))
"""


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
