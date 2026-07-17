from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from admin.backend.internal import jwks
from admin.backend.internal.jwks import verify_jwks_token
from admin.backend.middleware import decode_session_token
from pilot.core.admin_auth import issue_token

JWKS_URL = "https://issuer.example.com/.well-known/jwks.json"
AUDIENCE = "bench-a"  # every bench binds remote tokens to its own audience

# One RSA and one EC keypair the "remote issuer" signs with; the public halves
# are published in the stubbed JWKS document below.
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_EC = ec.generate_private_key(ec.SECP256R1())


def _jwks_document() -> dict:
    rsa_jwk = json.loads(RSAAlgorithm.to_jwk(_RSA.public_key()))
    rsa_jwk.update(kid="rsa-key", alg="RS256", use="sig")
    ec_jwk = json.loads(ECAlgorithm.to_jwk(_EC.public_key()))
    ec_jwk.update(kid="ec-key", alg="ES256", use="sig")
    return {"keys": [rsa_jwk, ec_jwk]}


def _mint(key=_RSA, alg: str = "RS256", kid: str = "rsa-key", **claims) -> str:
    payload = {"sub": "admin", "scope": "bench", "aud": AUDIENCE, "exp": int(time.time()) + 300, **claims}
    if payload.get("aud") is None:  # _mint(aud=None) omits the claim entirely
        payload.pop("aud")
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture(autouse=True)
def _stub_fetch(monkeypatch):
    monkeypatch.setattr(PyJWKClient, "fetch_data", lambda self: _jwks_document())
    jwks._clients.clear()
    yield
    jwks._clients.clear()


# ── verifier ──────────────────────────────────────────────────────────────────


def test_rsa_token_verifies() -> None:
    claims = verify_jwks_token(_mint(scope="site", site="a.com"), JWKS_URL, AUDIENCE)
    assert claims and claims["site"] == "a.com"


def test_ec_token_verifies() -> None:
    # The reason for switching to PyJWT: EC (ES256) keys work too.
    claims = verify_jwks_token(_mint(_EC, alg="ES256", kid="ec-key"), JWKS_URL, AUDIENCE)
    assert claims and claims["sub"] == "admin"


def test_expired_token_rejected() -> None:
    assert verify_jwks_token(_mint(exp=int(time.time()) - 10), JWKS_URL, AUDIENCE) is None


def test_token_without_exp_rejected() -> None:
    # A non-expiring token is refused outright (PyJWT does not require exp by
    # default); this also keeps the login endpoint from reading a missing exp.
    forever = jwt.encode({"sub": "admin", "scope": "bench", "jti": "x", "aud": AUDIENCE}, _RSA, algorithm="RS256", headers={"kid": "rsa-key"})
    assert verify_jwks_token(forever, JWKS_URL, AUDIENCE) is None


def test_tampered_signature_rejected() -> None:
    assert verify_jwks_token(_mint()[:-4] + "AAAA", JWKS_URL, AUDIENCE) is None


def test_unknown_kid_rejected() -> None:
    assert verify_jwks_token(_mint(kid="rotated-away"), JWKS_URL, AUDIENCE) is None


def test_symmetric_algorithm_not_accepted() -> None:
    # A published public key must never be replayable as an HMAC secret.
    forged = jwt.encode({"sub": "admin", "aud": AUDIENCE, "exp": int(time.time()) + 300}, "x" * 32, algorithm="HS256", headers={"kid": "rsa-key"})
    assert verify_jwks_token(forged, JWKS_URL, AUDIENCE) is None


def test_no_jwks_url_rejected() -> None:
    assert verify_jwks_token(_mint(), "", AUDIENCE) is None


# ── audience binding ──────────────────────────────────────────────────────────


def test_audience_accepted_when_matching() -> None:
    assert verify_jwks_token(_mint(aud="bench-a"), JWKS_URL, "bench-a")


def test_audience_rejected_when_mismatched() -> None:
    assert verify_jwks_token(_mint(aud="bench-b"), JWKS_URL, "bench-a") is None


def test_audience_required_but_absent_rejected() -> None:
    assert verify_jwks_token(_mint(aud=None), JWKS_URL, "bench-a") is None


def test_no_audience_config_rejects_remote_token() -> None:
    # Audience is mandatory for JWKS: with no configured audience a remote token
    # is not bound to this bench, so verification fails closed.
    assert verify_jwks_token(_mint(aud="anything"), JWKS_URL, "") is None


# ── unified session decoding ────────────────────────────────────────────────


class _Config:
    class admin:
        jwt_secret = "local-secret"
        jwks_url = JWKS_URL
        jwks_audience = AUDIENCE


def test_session_decode_accepts_local_secret() -> None:
    assert decode_session_token(issue_token("local-secret"), _Config)["scope"] == "bench"


def test_session_decode_falls_back_to_jwks() -> None:
    assert decode_session_token(_mint(), _Config)["sub"] == "admin"


def test_session_decode_rejects_unknown() -> None:
    assert decode_session_token(issue_token("stranger"), _Config) is None


def test_session_decode_skips_jwks_when_unconfigured() -> None:
    class NoJwks:
        class admin:
            jwt_secret = "local-secret"
            jwks_url = ""

    assert decode_session_token(_mint(), NoJwks) is None


# ── admin backend integration ─────────────────────────────────────────────────


def _client(tmp_path: Path):
    from admin.backend.app import create_app
    from pilot.config.bench import BenchConfig
    from pilot.config.bench_toml_builder import BenchTomlBuilder
    from pilot.config.toml_store import BenchTomlStore

    bench_root = tmp_path / "benches" / "current"
    bench_root.mkdir(parents=True)
    toml_path = bench_root / "bench.toml"
    toml_path.write_text(BenchTomlBuilder(bench_root.name, {"admin_enabled": True, "admin_password": "secret"}).render())
    config = BenchConfig.from_file(toml_path)
    config.admin.jwt_secret = "local-secret"
    config.admin.jwks_url = JWKS_URL
    config.admin.jwks_audience = AUDIENCE
    BenchTomlStore(toml_path).write(config)
    (bench_root / "env" / "bin").mkdir(parents=True)
    (bench_root / "env" / "bin" / "python").touch()
    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app.test_client()


def test_jwks_bearer_token_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/v1/benches", headers={"Authorization": f"Bearer {_mint()}"})
    assert resp.status_code != 401


def test_jwks_ec_bearer_token_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/v1/benches", headers={"Authorization": f"Bearer {_mint(_EC, alg='ES256', kid='ec-key')}"})
    assert resp.status_code != 401


def test_jwks_sid_login_with_jti_sets_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/v1/session", json={"sid": _mint(jti="login-1")})
    assert resp.status_code == 201
    assert resp.headers["Location"] == "/api/v1/session"
    assert client.get("/api/v1/benches").status_code != 401


def test_jwks_sid_login_requires_jti(tmp_path: Path) -> None:
    # A token without a jti must not be exchangeable for a session (else it is
    # replayable until expiry).
    client = _client(tmp_path)
    assert client.post("/api/v1/session", json={"sid": _mint()}).status_code == 401


def test_jwks_sid_login_is_single_use(tmp_path: Path) -> None:
    client = _client(tmp_path)
    sid = _mint(jti="login-2")
    assert client.post("/api/v1/session", json={"sid": sid}).status_code == 201
    assert client.post("/api/v1/session", json={"sid": sid}).status_code == 401


def test_jwks_sid_login_without_exp_does_not_crash(tmp_path: Path) -> None:
    client = _client(tmp_path)
    forever = jwt.encode({"sub": "admin", "scope": "bench", "jti": "noexp", "aud": AUDIENCE}, _RSA, algorithm="RS256", headers={"kid": "rsa-key"})
    assert client.post("/api/v1/session", json={"sid": forever}).status_code == 401


def test_jwks_site_scoped_token_cannot_bootstrap_session(tmp_path: Path) -> None:
    # A site-scoped token (even with a jti) must not escalate to a bench session.
    client = _client(tmp_path)
    resp = client.post("/api/v1/session", json={"sid": _mint(jti="login-3", scope="site", site="a.com")})
    assert resp.status_code == 401


def test_jwks_site_scoped_bearer_is_enforced(tmp_path: Path) -> None:
    client = _client(tmp_path)
    ok = client.get("/api/v1/sites/a.com/apps", headers={"Authorization": f"Bearer {_mint(scope='site', site='a.com')}"})
    denied = client.get("/api/v1/sites/a.com/apps", headers={"Authorization": f"Bearer {_mint(scope='site', site='other.com')}"})
    assert ok.status_code != 403
    assert denied.status_code == 403
