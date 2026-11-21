import sys
from pathlib import Path
from socket import socketpair
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pytest
from werkzeug.wrappers import Response

from pilot.managers.processes import web_handoff
from pilot.managers.processes.web_handoff import WebHandoff, check_sites


def test_handoff_drains_the_old_master_after_starting_the_replacement():
    server = WebHandoff([], Path("."))
    current = Mock()
    replacement = Mock()
    steps = []
    server.start_master = Mock(side_effect=lambda **kwargs: steps.append("ready") or replacement)
    server.drain = Mock(side_effect=lambda master: steps.append("drain"))

    control, client = socketpair()
    with client:
        client.sendall(b"reload\n")
        assert server.handle_connection(current, control) is replacement
        assert client.recv(256) == b"ok\n"

    assert steps == ["ready", "drain"]
    server.start_master.assert_called_once_with(check_health=True)
    server.drain.assert_called_once_with(current)


def test_handoff_keeps_old_master_when_replacement_fails_to_start():
    server = WebHandoff([], Path("."))
    current = Mock()
    server.start_master = Mock(side_effect=RuntimeError("replacement failed"))
    server.drain = Mock()

    control, client = socketpair()
    with client:
        client.sendall(b"reload\n")
        assert server.handle_connection(current, control) is current
        assert client.recv(256) == b"replacement failed\n"

    server.drain.assert_not_called()


def test_candidate_500_on_real_site_is_rejected_before_handoff(tmp_path, monkeypatch):
    site = tmp_path / "r1.localhost"
    site.mkdir()
    (site / "site_config.json").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "frappe", Mock())
    requests = []

    def application(environ, start_response):
        requests.append(environ["HTTP_HOST"])
        assert environ["pilot.internal_probe"] is True
        return Response("Application failed during initialization", status=500)(environ, start_response)

    with pytest.raises(RuntimeError, match=r"r1\.localhost: HTTP 500"):
        check_sites(application)
    assert requests == ["r1.localhost"]


def test_late_worker_finishes_after_handoff_markers_are_removed(tmp_path, monkeypatch):
    application = Mock()
    worker = Mock()
    application.run.side_effect = lambda: application.cfg.set.call_args.args[1](worker)
    monkeypatch.setitem(sys.modules, "gunicorn.app.wsgiapp", Mock(WSGIApplication=Mock(return_value=application)))
    monkeypatch.setattr(sys, "argv", [])
    with TemporaryDirectory(dir=tmp_path) as directory:
        hold = Path(directory) / "hold"
        hold.touch()
        hold.with_name("probe").touch()

        def remove_markers(_):
            for marker in hold.parent.iterdir():
                marker.unlink()
            hold.parent.rmdir()

        check_sites = Mock(side_effect=remove_markers)
        monkeypatch.setattr(web_handoff, "check_sites", check_sites)
        web_handoff.run_prepared_master(hold, ["gunicorn"])

    check_sites.assert_called_once_with(worker.wsgi)
