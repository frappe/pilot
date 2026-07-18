from __future__ import annotations

import json
import subprocess
import sys

import pilot.managers.task as task_manager
import pilot.managers.task.models as task_models
import pilot.tasks as tasks


def test_task_authoring_api_is_public_on_tasks_package() -> None:
    assert tasks.Arg.__name__ == "Arg"
    assert tasks.Arg.__module__ == "pilot.cli_args"
    assert tasks.BaseTask.__name__ == "BaseTask"
    assert callable(tasks.step)


def test_base_task_does_not_expose_subprocess_parser_plumbing() -> None:
    assert not hasattr(tasks.BaseTask, "parser")
    assert not hasattr(tasks.BaseTask, "from_args")
    assert not hasattr(tasks.BaseTask, "get_required_args")
    assert not hasattr(tasks, "apply_task_secrets")


def test_task_base_module_does_not_expose_step_implementation() -> None:
    import pilot.tasks.base as task_base

    assert not hasattr(task_base, "Step")


def test_task_submission_callback_types_are_public_on_tasks_package() -> None:
    assert tasks.TaskCallback.__name__ == "TaskCallback"
    assert tasks.TaskCallbacks.__name__ == "TaskCallbacks"
    assert tasks.TaskCallback.__module__ == "pilot.tasks.callbacks"
    assert tasks.TaskCallbacks.__module__ == "pilot.tasks.callbacks"


def test_task_runner_types_are_public_on_tasks_package() -> None:
    assert tasks.TaskRunner.__module__ == "pilot.tasks.runner"
    assert tasks.TaskSubmission.__module__ == "pilot.tasks.runner"


def test_task_runner_does_not_forward_engine_internals(tmp_path) -> None:
    runner = tasks.TaskRunner(tmp_path)

    assert not hasattr(runner, "_engine")
    assert not hasattr(runner, "_store")
    assert not hasattr(runner, "_generate_task_id")


def test_importing_task_base_does_not_discover_task_modules() -> None:
    script = (
        "import json, sys; "
        "import pilot.tasks.base; "
        "print(json.dumps(sorted("
        "name for name in sys.modules "
        "if name.startswith('pilot.tasks.') and name != 'pilot.tasks.base'"
        ")))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )

    assert json.loads(result.stdout) == []


def test_importing_tasks_package_does_not_load_runner_internals() -> None:
    script = (
        "import json, sys; "
        "import pilot.tasks; "
        "forbidden = {"
        "'pilot.core.bench', "
        "'pilot.internal.tasks.process', "
        "'pilot.internal.tasks.queue', "
        "'pilot.managers.task.reader', "
        "'pilot.internal.tasks.runner', "
        "'pilot.internal.tasks.store', "
        "'pilot.internal.tasks.worker'"
        "}; "
        "print(json.dumps(sorted(forbidden & set(sys.modules))))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )

    assert json.loads(result.stdout) == []


def test_internal_runner_does_not_import_public_runner_wrapper() -> None:
    script = (
        "import json, sys; "
        "import pilot.internal.tasks.runner; "
        "print(json.dumps('pilot.tasks.runner' in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )

    assert json.loads(result.stdout) is False


def test_task_registry_internals_are_not_public_on_tasks_package() -> None:
    assert not hasattr(tasks, "JOBS")
    assert not hasattr(tasks, "WHITELIST")
    assert not hasattr(tasks, "discover_tasks")
    assert not hasattr(tasks, "task_registry")
    assert not hasattr(tasks, "runner_class")


def test_task_authoring_api_is_not_reexported_from_manager_package() -> None:
    assert not hasattr(task_manager, "BaseTask")
    assert not hasattr(task_manager, "step")


def test_task_manager_package_exports_only_admin_runtime_api() -> None:
    assert set(task_manager.__all__) == {
        "TaskActivityReader",
        "TaskReader",
        "TaskStatus",
        "TaskWorkerControl",
        "sse_message",
        "task_requires_secrets",
    }


def test_task_models_do_not_expose_store_internals() -> None:
    assert not hasattr(task_models, "TaskCreation")
    assert not hasattr(task_models, "ACTIVE_TASK_STATUSES")
    assert not hasattr(task_models, "TERMINAL_TASK_STATUSES")
    assert not hasattr(task_models, "ALLOWED_TASK_TRANSITIONS")
    assert not hasattr(task_models, "parse_task_status")
    assert not hasattr(task_models, "validate_task_transition")
    assert not hasattr(task_models, "safe_task_failure")


def test_task_manager_public_exports_resolve_lazily() -> None:
    from pilot.managers.task import (
        TaskReader,
        TaskStatus,
        TaskWorkerControl,
        task_requires_secrets,
    )

    assert TaskReader.__name__ == "TaskReader"
    assert TaskStatus.QUEUED.value == "queued"
    assert TaskStatus.QUEUED.is_active is True
    assert TaskWorkerControl.__name__ == "TaskWorkerControl"
    assert TaskWorkerControl.__module__ == "pilot.managers.task.control"
    assert task_requires_secrets.__module__ == "pilot.managers.task.policy"


def test_task_retry_secret_policy_is_public() -> None:
    from pilot.managers.task import task_requires_secrets

    assert task_requires_secrets("new-site") is True
    assert task_requires_secrets("migrate") is False
