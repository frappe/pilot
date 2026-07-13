"""Tests for the authorized_keys store (pilot.core.ssh_keys)."""
from __future__ import annotations

import pytest

from pilot.core.ssh_keys import AuthorizedKeysStore, SSHKeyError

# Real keys; fingerprints cross-checked against `ssh-keygen -lf`.
ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILy2dBvqIXocjo05vcMZnMBRje9nYWi5k1e8Hy/GIl3A alice@example.com"
ED25519_FP = "SHA256:bJnE8PiSW6WTGg6SJL+YuJLt2RZ+WeId5NsjdrDlRXE"
RSA = (
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDDWjHTLhNKgp0DzgnYSDbFi+U3TvAMlQCZT0TTkL/EqXoS"
    "DC+QfuFRiqmevqDxD841uKqhJT/UKSvmkPYP5Zar5MHgDmt1FCtevuwulECAO28SuMELoFiQeBqWTBF8524l"
    "rgMHmoN2/jmFysAp/zmcCOYJWSsR/WCVUGJ5pzHVqYzakEELG45c1WJP9Z5tZSKnRkUiJ8jmt32tPGk1f5JG"
    "XqakGY645NmUBFlQ71GmJC5Ob0QkSov3NWO7E5xg0u5V7tDaRAOJhbYUlAL3CLxuFhHcdnzY56n4t1OYN/lG"
    "ob79gHhYUwTGIyd14IpP5niWyux+m64zFDquDQwHtD+3 bob@example.com"
)
RSA_FP = "SHA256:pQqcRYHWO0kJwaYa6//DYV5oJphkdTVyOCwHpGvkq3Y"


@pytest.fixture
def store(tmp_path):
    return AuthorizedKeysStore(path=tmp_path / ".ssh" / "authorized_keys")


def test_list_missing_file_returns_empty(store):
    assert store.list() == []


def test_add_parses_type_fingerprint_and_comment(store):
    key = store.add(ED25519)
    assert key.key_type == "ssh-ed25519"
    assert key.fingerprint == ED25519_FP
    assert key.comment == "alice@example.com"


def test_add_computes_rsa_fingerprint(store):
    assert store.add(RSA).fingerprint == RSA_FP


def test_add_creates_ssh_dir_and_file_with_restrictive_modes(store):
    store.add(ED25519)
    assert store.path.exists()
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


def test_add_rejects_garbage(store):
    with pytest.raises(SSHKeyError):
        store.add("not a key")


def test_add_rejects_type_blob_mismatch(store):
    # ed25519 type token with the rsa blob → embedded algorithm won't match.
    mangled = "ssh-ed25519 " + RSA.split()[1]
    with pytest.raises(SSHKeyError):
        store.add(mangled)


def test_add_is_idempotent_on_duplicate(store):
    store.add(ED25519)
    with pytest.raises(SSHKeyError):
        store.add(ED25519)
    assert len(store.list()) == 1


def test_remove_deletes_by_fingerprint(store):
    store.add(ED25519)
    store.add(RSA)
    store.remove(ED25519_FP)
    remaining = store.list()
    assert [k.fingerprint for k in remaining] == [RSA_FP]


def test_remove_preserves_comments_and_blank_lines(store):
    store.path.parent.mkdir(mode=0o700, parents=True)
    store.path.write_text(f"# managed keys\n\n{ED25519}\n{RSA}\n")
    store.remove(ED25519_FP)
    text = store.path.read_text()
    assert "# managed keys" in text
    assert "\n\n" in text
    assert ED25519.split()[1] not in text


def test_remove_last_key_is_refused(store):
    store.add(ED25519)
    with pytest.raises(SSHKeyError, match="last"):
        store.remove(ED25519_FP)
    assert len(store.list()) == 1


def test_remove_unknown_fingerprint_raises(store):
    store.add(ED25519)
    store.add(RSA)
    with pytest.raises(SSHKeyError, match="matches"):
        store.remove("SHA256:doesnotexist")


def test_writes_are_atomic_and_leave_no_temp_files(store):
    store.add(ED25519)
    store.add(RSA)
    store.remove(ED25519_FP)
    leftovers = list(store.path.parent.glob(".authorized_keys-*"))
    assert leftovers == []
    assert store.path.stat().st_mode & 0o777 == 0o600
