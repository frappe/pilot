import json

from pilot.tasks.manager.events import (
    done_event,
    output_event,
    sse_message,
    status_event,
)


def test_sse_message_encodes_structured_json_with_event_id() -> None:
    message = sse_message(output_event("build: 50%", overwrite=True), event_id=7)

    lines = message.strip().splitlines()
    assert lines[0] == "id: 7"
    assert json.loads(lines[1].removeprefix("data: ")) == {
        "type": "overwrite",
        "line": "build: 50%",
    }


def test_output_text_cannot_impersonate_completion_event() -> None:
    legacy_marker = "__" + "DONE__:0"
    event = output_event(legacy_marker)

    assert event == {"type": "line", "line": legacy_marker}
    assert json.loads(sse_message(event).removeprefix("data: ")) == event


def test_done_event_keeps_terminal_details() -> None:
    assert done_event("killed", None, None) == {
        "type": "done",
        "status": "killed",
        "exit_code": None,
        "failure": None,
    }


def test_status_event_reports_queue_position() -> None:
    assert status_event("queued", 2) == {
        "type": "status",
        "status": "queued",
        "queue_position": 2,
    }
