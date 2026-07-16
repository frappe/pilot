from pathlib import Path

from flask import url_for

from admin.backend.api_contract import accepted_response
from pilot.tasks.manager.task_reader import TaskReader


def accepted_task_response(bench_root: Path, task_id: str):
    task = TaskReader(bench_root).read_task(task_id)
    return accepted_response(
        task.as_dict(),
        url_for("tasks.get_task", task_id=task_id),
    )
