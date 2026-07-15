"""Background-task helpers.

Every mutating admin action (create site, install/uninstall app, drop site) runs
as a forked background task. The UI fires the request, gets a task_id, and
streams progress. For tests we capture that task_id from the response and then
poll the task API to completion — far more robust than racing UI toasts.
"""

from __future__ import annotations

import time
from typing import Callable

from playwright.sync_api import APIRequestContext, Page, expect


def run_task_action(page: Page, url_fragment: str, action: Callable[[], None]) -> str:
    """Run a UI action that kicks off a background task and return its task_id.

    Pass the URL fragment the action POSTs to (e.g. 'create', 'install-app',
    'drop') so we wait for the right response.
    """
    with page.expect_response(
        lambda r: url_fragment in r.url and r.request.method == "POST"
    ) as response_info:
        action()
    body = response_info.value.json()
    if not body.get("ok") or not body.get("task_id"):
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
            status = data["task"]["status"]
            if status == "success":
                return
            if status == "failed":
                tail = "\n".join((data.get("output") or [])[-30:])
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
    res = request.get(f"{base_url}/api/v1/status")
    expect(res).to_be_ok()
    assert res.json().get("wizard") is not True
