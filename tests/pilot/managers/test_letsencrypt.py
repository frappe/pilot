from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import CommandError
from pilot.managers.letsencrypt import LetsEncryptManager

_BASE_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}


def _make_bench(tmp_path: Path, data: dict) -> Bench:
    return Bench(BenchConfig._from_dict(data), tmp_path)


def test_setup_sudoers_grants_only_certbot_and_cert_reads(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = LetsEncryptManager(bench)
    sudoers_file = Path("/etc/sudoers.d/runner-pilot-certbot")

    with (
        patch("pwd.getpwuid") as mock_getpwuid,
        patch("pilot.managers.sudoers.stage_and_copy") as mock_stage,
        patch("pilot.managers.sudoers.run_command") as mock_run,
        patch.object(LetsEncryptManager, "has_passwordless_sudo", False),
    ):
        mock_getpwuid.return_value.pw_name = "runner"
        manager.setup_sudoers()

    content, target = mock_stage.call_args.args[1:3]
    assert mock_stage.call_args.kwargs == {"validate": ["visudo", "-cf"]}
    assert target == sudoers_file
    assert "runner ALL=(ALL) NOPASSWD:" in content
    # no bare trailing wildcard anywhere - every "*" is anchored by fixed text
    # on both sides, so nothing can be appended or substituted after a match.
    for clause in content.removeprefix("runner ALL=(ALL) NOPASSWD: ").rstrip("\n").split(","):
        assert not clause.rstrip().endswith("*"), f"unanchored trailing wildcard: {clause!r}"
    assert "certonly --webroot -w /var/www/letsencrypt * --cert-name * --expand --email *" in content
    # unquoted: the real invocation never contains a literal `"` byte to match against
    assert "--deploy-hook systemctl reload nginx" in content
    assert '"' not in content
    assert "certonly --webroot -w /var/www/letsencrypt -d * --email *" in content
    assert "certbot renew --quiet," in content
    assert "certbot renew *" not in content
    assert "mkdir -p /var/www/letsencrypt," in content
    # cert_files_exist() runs this as the bench user; without it every cert reads
    # as missing and generate_config renders the whole bench HTTP-only.
    assert (
        "test -f /etc/letsencrypt/live/*/fullchain.pem -a -f /etc/letsencrypt/live/*/privkey.pem" in content
    )
    assert "-in /etc/letsencrypt/live/*/fullchain.pem" in content
    assert content.count("openssl") == 2
    assert "ALL=(ALL) NOPASSWD: ALL" not in content

    mock_run.assert_called_once()
    assert mock_run.call_args.args[0][-3:] == ["chmod", "440", str(sudoers_file)]


def test_has_passwordless_sudo_checks_certbot_grant(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = LetsEncryptManager(bench)

    with patch("pilot.managers.letsencrypt.has_passwordless_sudo_for") as mock_check:
        mock_check.return_value = True
        assert manager.has_passwordless_sudo is True

    checked_command = mock_check.call_args.args[0]
    assert checked_command[-2:] == ["renew", "--quiet"]


def _certbot(returncode: int, output: str = ""):
    import subprocess

    return subprocess.CompletedProcess([], returncode, output, "")


_LOCKED = _certbot(1, "Another instance of Certbot is already running.\n")
_CHALLENGE_FAILED = """Saving debug log to /var/log/letsencrypt/letsencrypt.log
Certbot failed to authenticate some domains (authenticator: webroot).
  Domain: shop.example.com
  Type:   connection
  Detail: Timeout during connect (likely firewall problem)

Some challenges have failed.
"""


def test_certbot_waits_while_another_run_holds_its_lock(tmp_path: Path) -> None:
    manager = LetsEncryptManager(_make_bench(tmp_path, _BASE_DATA))

    with (
        patch("pilot.managers.letsencrypt.run_command", side_effect=[_LOCKED, _LOCKED, _certbot(0)]) as certbot,
        patch("pilot.managers.letsencrypt.time.sleep") as sleep,
    ):
        manager.run_certbot(["certbot", "certonly"])

    assert certbot.call_count == 3
    assert sleep.call_count == 2
    assert certbot.call_args.kwargs == {"tee_output": True}


def test_certbot_gives_a_clear_error_when_the_lock_stays_held(tmp_path: Path) -> None:
    manager = LetsEncryptManager(_make_bench(tmp_path, _BASE_DATA))

    with (
        patch("pilot.managers.letsencrypt.run_command", return_value=_LOCKED) as certbot,
        patch("pilot.managers.letsencrypt.time.sleep"),
        pytest.raises(CommandError, match="Another certbot run is in progress"),
    ):
        manager.run_certbot(["certbot", "certonly"])

    assert certbot.call_count == 6


def test_certbot_does_not_retry_other_failures_and_keeps_its_reason(tmp_path: Path) -> None:
    manager = LetsEncryptManager(_make_bench(tmp_path, _BASE_DATA))

    with (
        patch("pilot.managers.letsencrypt.run_command", return_value=_certbot(1, _CHALLENGE_FAILED)) as certbot,
        patch("pilot.managers.letsencrypt.time.sleep") as sleep,
        pytest.raises(CommandError) as raised,
    ):
        manager.run_certbot(["certbot", "certonly"])

    assert raised.value.message == (
        "Domain: shop.example.com\nType:   connection\nDetail: Timeout during connect (likely firewall problem)"
    )
    assert certbot.call_count == 1
    sleep.assert_not_called()
