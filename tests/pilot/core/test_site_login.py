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
