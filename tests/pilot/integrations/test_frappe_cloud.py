from __future__ import annotations

import io
import json
import urllib.error

import pytest

from pilot.exceptions import FrappeCloudError
from pilot.integrations.frappe_cloud import get_error_message, open_download_link


def _http_error(body: bytes, code: int = 417) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://cloud.example.com", code, "error", {}, io.BytesIO(body))


def test_frappes_own_message_explains_a_refusal() -> None:
    messages = json.dumps([json.dumps({"message": "The pass code is not correct."})])
    body = json.dumps({"exc_type": "ValidationError", "_server_messages": messages}).encode()

    assert get_error_message(_http_error(body)) == "The pass code is not correct."


def test_a_response_without_a_message_reports_the_http_status() -> None:
    assert get_error_message(_http_error(b"<html>Bad gateway</html>", 502)) == "Frappe Cloud answered with HTTP 502."


@pytest.mark.parametrize("url", ["file:///etc/hostname", "ftp://example.com/backup.sql.gz", "/etc/hostname"])
def test_a_download_link_that_is_not_http_is_refused(url: str) -> None:
    with pytest.raises(FrappeCloudError, match="not HTTP"):
        open_download_link(url)
