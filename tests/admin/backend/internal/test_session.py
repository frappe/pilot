from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from admin.backend.internal.session import ActiveTokens, Session
from pilot.config import BenchConfig
from pilot.core.bench import Bench


def _bench(tmp_path: Path, password: str = "secret", jwt_secret: str | None = None) -> Bench:
    tmp_path.mkdir(parents=True, exist_ok=True)
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text(BenchConfig.from_flat(tmp_path.name, {"admin_password": password}).dumps())
    if jwt_secret is not None:
        config = BenchConfig.from_file(toml_path)
        config.admin.jwt_secret = jwt_secret
        config.write(toml_path)
    return Bench(BenchConfig.from_file(toml_path), tmp_path)


def test_issue_session_token_is_verifiable_and_active(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    token, jti = session.issue_session_token()

    claims = session.verify_token(token)
    assert claims["jti"] == jti
    assert claims["scope"] == "bench"
    assert jti in ActiveTokens(session.bench)


def test_ensure_jwt_secret_generates_and_persists(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    assert not bench.config.admin.jwt_secret

    secret = Session(bench).ensure_jwt_secret()
    assert secret
    assert BenchConfig.from_file(tmp_path / "bench.toml").admin.jwt_secret == secret

    reloaded = Bench(BenchConfig.from_file(tmp_path / "bench.toml"), tmp_path)
    assert Session(reloaded).ensure_jwt_secret() == secret


def test_verify_token_without_secret_returns_none(tmp_path: Path) -> None:
    assert Session(_bench(tmp_path)).verify_token("anything") is None


def test_verify_token_rejects_foreign_secret(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path / "mine", jwt_secret="k3y"))
    other = Session(_bench(tmp_path / "other", jwt_secret="other-secret"))
    assert session.verify_token(other.issue_session_token()[0]) is None


def test_verify_token_rejects_tampered_token(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    token, _ = session.issue_session_token()
    assert session.verify_token(token[:-4] + "AAAA") is None


def test_verify_token_rejects_expired_token(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    expired, _ = session.issue_session_token(ttl=-10)
    assert session.verify_token(expired) is None


def test_issue_site_token_is_scoped(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    claims = session.verify_token(session.issue_site_token("a.com"))
    assert claims["scope"] == "site"
    assert claims["site"] == "a.com"


def test_issue_site_token_requires_site(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Session(_bench(tmp_path)).issue_site_token("")


def test_issue_site_token_custom_ttl(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    claims = session.verify_token(session.issue_site_token("a.com", ttl=3600))
    assert claims["exp"] - claims["iat"] == 3600


def test_issue_pilot_token_lives_for_one_year(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    claims = session.verify_token(session.issue_pilot_token("a.com"))

    assert claims["exp"] - claims["iat"] == 365 * 24 * 3600


def test_revoke_token_rejects_a_site_token_until_expiry(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    token = session.issue_site_token("a.com")

    assert session.revoke_token(token)
    assert session.verify_token(token) is None


def test_revoke_token_accepts_a_post_commit_sync_failure(tmp_path: Path) -> None:
    from admin.backend.internal.session import RevokedTokens

    session = Session(_bench(tmp_path))
    token = session.issue_site_token("a.com")
    add = RevokedTokens.add

    def commit_then_fail(store, key, exp):
        add(store, key, exp)
        raise OSError("directory sync failed")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(RevokedTokens, "add", commit_then_fail)
        assert session.revoke_token(token)

    assert session.verify_token(token) is None


def test_issue_login_token_carries_jti(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    assert session.verify_token(session.issue_login_token())["jti"]


def test_revoked_jtis_are_shared_across_sessions(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    token, jti = Session(bench).issue_session_token()
    Session(bench).revoke_jti(jti)
    assert Session(bench).verify_token(token) is None


def test_revoke_jti_revokes_active_session(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    token, jti = session.issue_session_token()
    assert session.revoke_jti(jti) is True
    assert session.verify_token(token) is None


def test_revoke_jti_unknown_returns_false(tmp_path: Path) -> None:
    assert Session(_bench(tmp_path)).revoke_jti("not-a-session") is False


def test_active_jtis_tracks_issued_and_drops_revoked(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    _, jti_a = session.issue_session_token()
    _, jti_b = session.issue_session_token()
    assert set(session.active_jtis()) == {jti_a, jti_b}

    session.revoke_jti(jti_a)
    assert set(session.active_jtis()) == {jti_b}


def test_verify_registers_unseen_valid_jti(tmp_path: Path) -> None:
    session = Session(_bench(tmp_path))
    token, jti = session.issue_session_token()
    # Simulate the tracker not knowing this jti yet (e.g. issued by another worker).
    (session.bench.path / ActiveTokens.FILENAME).unlink()
    assert jti not in ActiveTokens(session.bench)

    assert session.verify_token(token) is not None
    assert jti in ActiveTokens(session.bench)


def test_expired_jtis_are_purged_from_disk_on_read(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    path = bench.path / ActiveTokens.FILENAME
    path.write_text(
        json.dumps(
            {
                "dead": {"exp": int(time.time()) - 5, "ip": "1.1.1.1", "last_seen": 0},
                "live": {"exp": int(time.time()) + 300, "ip": "1.1.1.1", "last_seen": 0},
            }
        )
    )

    assert set(ActiveTokens(bench).all()) == {"live"}  # read drops the expired entry
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"live"}  # ...from disk too


def test_revoked_jti_is_pruned_once_its_own_expiry_passes(tmp_path: Path) -> None:
    from admin.backend.internal.session import RevokedTokens

    bench = _bench(tmp_path)
    path = bench.path / RevokedTokens.FILENAME
    path.write_text(json.dumps({"gone": int(time.time()) - 5, "still-revoked": int(time.time()) + 300}))

    assert set(RevokedTokens(bench).all()) == {"still-revoked"}
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"still-revoked"}
