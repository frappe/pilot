"""AI-assisted debugging of a failed task."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from pilot.config.llm import LLMConfig
from pilot.integrations.llm.base import LLMError
from pilot.integrations.llm.registry import build_integration
from pilot.managers.task.models import TaskInfo

_OUTPUT_LIMIT = 20_000


def build_debug_prompt(task: TaskInfo, output: str) -> str:
    """Frame a failed task's command and output for the model."""
    tail = output[-_OUTPUT_LIMIT:]
    truncated = "[...earlier output truncated...]\n" if len(output) > _OUTPUT_LIMIT else ""
    args = json.dumps(task.args) if task.args else "{}"
    return (
        "A bench task failed. Explain the likely cause and the concrete steps to fix it.\n\n"
        f"Command: {task.command}\n"
        f"Arguments: {args}\n"
        f"Exit code: {task.exit_code}\n\n"
        f"Output:\n{truncated}{tail}"
    )


def stream_task_debug(
    llm_config: LLMConfig, task: TaskInfo, output: str, *, bench_root: Path
) -> Iterator[str]:
    """Yield text deltas of the model's explanation; raises LLMError on failure."""
    integration = build_integration(llm_config, stream=True)
    response = integration.prompt(
        build_debug_prompt(task, output),
        bench_root=bench_root,
        max_tokens=llm_config.max_tokens,
    )
    try:
        yield from integration.iter_response_text(response)
    except LLMError:
        raise
    except Exception as exc:  # a provider hiccup mid-stream
        raise LLMError(f"{llm_config.provider} stopped responding mid-answer.") from exc
