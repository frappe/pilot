import threading
import time
from unittest.mock import patch

from pilot.core.site.login import _resolver, is_host_resolvable


def test_localhost_names_resolve_without_a_lookup() -> None:
    with patch("pilot.core.site.login.socket.getaddrinfo") as getaddrinfo:
        assert is_host_resolvable("site.localhost") is True
        assert is_host_resolvable("localhost") is True

    getaddrinfo.assert_not_called()


def test_unresolvable_host_is_reported() -> None:
    with patch("pilot.core.site.login.socket.getaddrinfo", side_effect=OSError):
        assert is_host_resolvable("nowhere.example.test") is False


def test_slow_lookup_counts_as_resolvable() -> None:
    def slow_lookup(*args):
        time.sleep(0.3)

    with patch("pilot.core.site.login.socket.getaddrinfo", side_effect=slow_lookup):
        assert is_host_resolvable("slow.example.test", timeout=0.05) is True


def test_timed_out_lookups_are_cancelled_and_do_not_queue_up() -> None:
    _resolver.submit(lambda: None).result(timeout=2)  # wait out earlier tests' lookups
    release = threading.Event()
    executed_hosts = []

    def blocked_lookup(host, port):
        executed_hosts.append(host)
        release.wait(2)

    with patch("pilot.core.site.login.socket.getaddrinfo", side_effect=blocked_lookup):
        for index in range(5):
            assert is_host_resolvable(f"host-{index}.example.test", timeout=0.01) is True
        release.set()
        time.sleep(0.2)

    assert executed_hosts == ["host-0.example.test"]


def test_timed_out_lookups_do_not_accumulate_resolver_threads() -> None:
    def slow_lookup(*args):
        time.sleep(0.2)

    with patch("pilot.core.site.login.socket.getaddrinfo", side_effect=slow_lookup):
        for _ in range(5):
            assert is_host_resolvable("slow.example.test", timeout=0.01) is True

    resolver_threads = [t for t in threading.enumerate() if t.name.startswith("host-resolver")]
    assert len(resolver_threads) <= 1


_FAKE_FRAPPE = {
    "frappe/__init__.py": """
import json, os

def _log(*event):
    with open(os.environ["FAKE_FRAPPE_LOG"], "a") as log:
        log.write(json.dumps(event) + "\\n")

class _dict(dict):
    __getattr__ = dict.get

class db:
    @staticmethod
    def exists(doctype, name):
        return name in os.environ["FAKE_FRAPPE_USERS"].split(",")

    @staticmethod
    def commit():
        pass

class utils:
    @staticmethod
    def set_request(path):
        pass

class session:
    sid = "fake-sid"

class local:
    pass

def init(site, sites_path):
    pass

def connect():
    pass
""",
    "frappe/auth.py": """
import frappe

class CookieManager:
    pass

class LoginManager:
    def login_as(self, user):
        frappe._log("login_as", user)
""",
    "frappe/desk/__init__.py": "",
    "frappe/desk/page/__init__.py": "",
    "frappe/desk/page/setup_wizard/__init__.py": "",
    "frappe/desk/page/setup_wizard/setup_wizard.py": """
import frappe

def create_or_update_user(args):
    frappe._log("create_user", args.email, args.full_name)
""",
}


def _sign_in(tmp_path, monkeypatch, user: str, existing_users: str = "") -> tuple[str | None, list]:
    """Run the real sign-in program against a stand-in Frappe that records what it is asked."""
    import json
    import sys
    from types import SimpleNamespace

    from pilot.core.site.login import SiteLogin

    for path, source in _FAKE_FRAPPE.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    log = tmp_path / "events.jsonl"
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("FAKE_FRAPPE_LOG", str(log))
    monkeypatch.setenv("FAKE_FRAPPE_USERS", existing_users)
    bench = SimpleNamespace(python=sys.executable, sites_path=tmp_path)
    site = SimpleNamespace(bench=bench, config=SimpleNamespace(name="site.local"))

    sid = SiteLogin(site).create_session(user, "Asha Rao")
    events = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return sid, events


def test_a_user_the_site_lacks_is_created_then_signed_in(tmp_path, monkeypatch) -> None:
    sid, events = _sign_in(tmp_path, monkeypatch, "asha@example.com")

    assert sid == "fake-sid"
    assert events == [["create_user", "asha@example.com", "Asha Rao"], ["login_as", "asha@example.com"]]


def test_an_existing_user_and_administrator_are_only_signed_in(tmp_path, monkeypatch) -> None:
    _, existing = _sign_in(tmp_path, monkeypatch, "asha@example.com", existing_users="asha@example.com")
    (tmp_path / "events.jsonl").unlink()
    _, administrator = _sign_in(tmp_path, monkeypatch, "Administrator")

    assert existing == [["login_as", "asha@example.com"]]
    assert administrator == [["login_as", "Administrator"]]
