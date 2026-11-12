from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from flask import current_app, request

from admin.backend.api.errors import ApiProblem
from admin.backend.api.responses import accepted_task_response

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class ApiRequest:
    """The current Admin API request: the bench it acts on, its JSON body and its idempotency key."""

    @staticmethod
    def get_bench_root() -> Path:
        """The directory of the bench this Admin serves."""
        return Path(current_app.config["BENCH_ROOT"])

    @classmethod
    def get_bench(cls) -> Bench:
        """The bench this Admin serves."""
        from pilot.core.bench import Bench

        return Bench(cls.get_bench_root())

    @staticmethod
    def get_json_body() -> dict:
        """The JSON object body; raise a 400 malformed_request problem for anything else."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiProblem("malformed_request", "Expected a JSON object.", 400)
        return data

    @staticmethod
    def get_idempotency_key() -> str | None:
        """The Idempotency-Key header, which makes a retried queue request return the first task."""
        return request.headers.get("Idempotency-Key")

    @classmethod
    def queue_task(cls, task_class: Any, bench: Bench, *args: Any, **kwargs: Any):
        """Queue the task with this request's idempotency key and answer 202 with the task."""
        task_id = task_class.queue(bench, *args, idempotency_key=cls.get_idempotency_key(), **kwargs)
        return accepted_task_response(bench.path, task_id)
