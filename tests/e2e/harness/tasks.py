"""Background-task helpers for admin e2e tests."""

from __future__ import annotations

import time
from collections.abc import Callable

from playwright.sync_api import APIRequestContext, Page, expect


def run_task_action(
    page: Page,
    url_fragment: str,
    action: Callable[[], None],
    method: str = "POST",
) -> str:
    """Run a UI action and return the task_id from its API response."""
    with page.expect_response(
        lambda r: url_fragment in r.url and r.request.method == method
    ) as response_info:
        action()
    body = response_info.value.json()
    if not body.get("task_id"):
        raise RuntimeError(f'Action "{url_fragment}" did not start a task: {body}')
    return body["task_id"]


def wait_for_task(
    request: APIRequestContext,
    base_url: str,
    task_id: str,
    timeout: float = 30 * 60,
) -> None:
    """Poll /api/v1/tasks/:id until it succeeds; raise with the output tail on failure."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = request.get(f"{base_url}/api/v1/tasks/{task_id}")
        if res.ok:
            data = res.json()
            status = data["status"]
            if status == "success":
                return
            if status == "failed":
                output = request.get(f"{base_url}/api/v1/tasks/{task_id}/output/content")
                tail = "\n".join(output.text().splitlines()[-30:]) if output.ok else ""
                raise RuntimeError(f"Task {task_id} failed:\n{tail}")
        time.sleep(2)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout}s")


def run_and_await_task(
    page: Page,
    base_url: str,
    url_fragment: str,
    action: Callable[[], None],
    timeout: float = 30 * 60,
) -> None:
    """Convenience: run a task-producing action and wait for it to succeed."""
    task_id = run_task_action(page, url_fragment, action)
    wait_for_task(page.request, base_url, task_id, timeout)


def expect_bench_online(request: APIRequestContext, base_url: str) -> None:
    """Sanity assert that the admin is reachable and out of wizard mode."""
    res = request.get(f"{base_url}/api/v1/bootstrap")
    expect(res).to_be_ok()
    assert res.json().get("mode") == "admin"
