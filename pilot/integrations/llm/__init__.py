from pathlib import Path

from pilot.internal.atomic_file import atomic_write_private_text

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "SYSTEM_PROMPT_FILENAME",
    "clear_system_prompt",
    "read_system_prompt",
    "system_prompt_path",
    "write_system_prompt",
]


SYSTEM_PROMPT_FILENAME = ".system-prompt.txt"

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful frappe assistant. Answer as concisely as possible. "
    "Help me fix the migration/task errors that you see in the logs. If you don't "
    'know the answer, just say "I don\'t know". Do not try to make up an answer.'
)


def system_prompt_path(bench_root: Path) -> Path:
    return bench_root / SYSTEM_PROMPT_FILENAME


def read_system_prompt(bench_root: Path) -> str:
    """The bench's system prompt, or the built-in default if none is saved."""
    path = system_prompt_path(bench_root)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return DEFAULT_SYSTEM_PROMPT


def write_system_prompt(bench_root: Path, text: str) -> None:
    atomic_write_private_text(system_prompt_path(bench_root), text)


def clear_system_prompt(bench_root: Path) -> None:
    system_prompt_path(bench_root).unlink(missing_ok=True)
