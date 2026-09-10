"""Tests for the email sink on the alert fan-out."""

import json
import smtplib
import ssl
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig, MariaDBConfig, RedisConfig, WorkerConfig
from pilot.config.alert_limit import ResourceLimitConfig
from pilot.config.mail import MailConfig, MalformedSiteConfig
from pilot.core.alerts import check_mail_credentials, notify, send_mail
from pilot.core.bench import Bench

PAYLOAD = {
    "event": "site_down",
    "message": "my-bench: a.test unreachable",
    "context": {"bench": "my-bench", "sites": ["a.test"]},
}


class FakeSMTP:
    """Stands in for both smtplib transports and records what a send did."""

    sends: ClassVar[list["FakeSMTP"]] = []
    refuse: ClassVar[dict] = {}  # recipients smtplib reports back as refused

    def __init__(self, host: str, port: int, timeout: float | None = None, context=None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.started_tls = False
        self.logged_in_as: tuple[str, str] | None = None
        self.messages: list = []
        FakeSMTP.sends.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exception) -> bool:
        return False

    def starttls(self, context=None) -> None:
        self.started_tls = True
        self.context = context

    def login(self, username: str, password: str) -> None:
        self.logged_in_as = (username, password)

    def send_message(self, message) -> dict:
        self.messages.append(message)
        return dict(FakeSMTP.refuse)


@pytest.fixture(autouse=True)
def _clear_sends():
    FakeSMTP.sends.clear()


def _mail(**overrides) -> MailConfig:
    mail = MailConfig(server="smtp.test", email="alerts@test", password="secret")
    for name, value in overrides.items():
        setattr(mail, name, value)
    return mail


RECIPIENTS = ["ops@test"]


def _bench(tmp_path: Path, mail: MailConfig, **limit_overrides) -> Bench:
    config = BenchConfig(
        name="my-bench",
        python_version="3.14",
        mariadb=MariaDBConfig(),
        redis=RedisConfig(),
        workers=WorkerConfig(),
    )
    config.resource_limits = ResourceLimitConfig(
        email_recipients=limit_overrides.pop("email_recipients", list(RECIPIENTS)),
        **limit_overrides,
    )
    bench = Bench(config, tmp_path / "my-bench")
    bench.sites_path.mkdir(parents=True, exist_ok=True)
    (bench.sites_path / "common_site_config.json").write_text("{}")
    mail.write(bench.sites_path)
    return bench


def test_the_alert_is_mailed_over_starttls() -> None:
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(), ["ops@test", "oncall@test"], PAYLOAD)

    sent = FakeSMTP.sends[0]
    message = sent.messages[0]
    assert (sent.host, sent.port) == ("smtp.test", 587)
    assert sent.started_tls
    assert sent.logged_in_as == ("alerts@test", "secret")
    assert message["To"] == "ops@test, oncall@test"
    assert message["Subject"] == "[Pilot] my-bench: a.test unreachable"
    # Both a plain-text and an HTML body, each carrying the alert.
    text = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    assert "a.test" in text
    assert "a.test" in html
    assert "<table" in html


def test_both_transports_verify_the_server_certificate() -> None:
    """smtplib's own default context skips verification, which would hand the
    password to whoever answers on an intercepted connection."""
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(), RECIPIENTS, PAYLOAD)
    with patch("smtplib.SMTP_SSL", FakeSMTP):
        send_mail(_mail(use_ssl=True), RECIPIENTS, PAYLOAD)

    for send in FakeSMTP.sends:
        assert send.context is not None
        assert send.context.verify_mode == ssl.CERT_REQUIRED
        assert send.context.check_hostname


def test_the_address_is_the_sender_and_the_default_login_name() -> None:
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(), RECIPIENTS, PAYLOAD)

    sent = FakeSMTP.sends[0]
    assert sent.messages[0]["From"] == "alerts@test"
    assert sent.logged_in_as == ("alerts@test", "secret")


def test_a_separate_login_name_does_not_change_the_sender() -> None:
    """The framework's Email Account allows a login that is not the address; the
    mail still has to come from the address the operator configured."""
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(login="alerts"), RECIPIENTS, PAYLOAD)

    sent = FakeSMTP.sends[0]
    assert sent.logged_in_as == ("alerts", "secret")
    assert sent.messages[0]["From"] == "alerts@test"


def test_ssl_connects_on_465() -> None:
    with patch("smtplib.SMTP_SSL", FakeSMTP):
        send_mail(_mail(use_ssl=True), RECIPIENTS, PAYLOAD)

    sent = FakeSMTP.sends[0]
    assert (sent.host, sent.port) == ("smtp.test", 465)
    assert not sent.started_tls


def test_a_configured_port_wins_over_the_default() -> None:
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(port=2525), RECIPIENTS, PAYLOAD)

    assert FakeSMTP.sends[0].port == 2525


def test_the_html_body_renders_the_breach_details_as_a_table() -> None:
    payload = {
        "event": "resource_limit_breached",
        "message": "my-bench: disk at 93%",
        "context": {
            "bench": "my-bench",
            "time": "2026-09-01T00:00:00+00:00",
            "breached_limits": [{"limit": "disk", "threshold": 85, "reading": 93}],
        },
    }
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(), RECIPIENTS, payload)

    html = FakeSMTP.sends[0].messages[0].get_body(("html",)).get_content()
    assert "<table" in html and "Threshold" in html and "93" in html
    # The body is for a person: no raw JSON dump.
    assert "{" not in html
    assert "Team Pilot" in html


def test_the_logo_is_embedded_inline_not_attached() -> None:
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(), RECIPIENTS, PAYLOAD)

    message = FakeSMTP.sends[0].messages[0]
    images = [p for p in message.walk() if p.get_content_type() == "image/png"]
    assert len(images) == 1
    assert images[0].get("Content-Disposition", "").startswith("inline")
    html = message.get_body(("html",)).get_content()
    assert f'cid:{images[0]["Content-ID"].strip("<>")}' in html


def test_an_unknown_event_still_mails_the_summary() -> None:
    payload = {"event": "brand_new", "message": "my-bench: something", "context": {"bench": "my-bench"}}
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(), RECIPIENTS, payload)

    message = FakeSMTP.sends[0].messages[0]
    assert "my-bench: something" in message.get_body(("plain",)).get_content()
    assert "my-bench: something" in message.get_body(("html",)).get_content()


def test_a_relay_without_a_password_sends_anonymously() -> None:
    with patch("smtplib.SMTP", FakeSMTP):
        send_mail(_mail(password=""), RECIPIENTS, PAYLOAD)

    sent = FakeSMTP.sends[0]
    assert sent.logged_in_as is None
    # An empty From becomes MAIL FROM:<>, which most relays refuse.
    assert sent.messages[0]["From"] == "alerts@test"


def test_a_refused_recipient_is_not_a_delivery(tmp_path: Path) -> None:
    """send_message reports per-recipient refusals by returning them; a partial
    send must not retire the alert for the mailbox that never got it."""
    refused = {"oncall@test": (550, b"No such user")}

    with patch("smtplib.SMTP", FakeSMTP), patch.object(FakeSMTP, "refuse", refused):
        delivered = notify(_bench(tmp_path, _mail()), PAYLOAD)

    assert not delivered


def test_broken_settings_disable_mail_instead_of_raising(tmp_path: Path) -> None:
    """common_site_config.json can be hand-edited, and most config reads skip
    validation, so bad settings have to read as "no mail sink" rather than kill
    the monitor tick."""
    for overrides in ({"email": "not-an-address"}, {"port": 70000}, {"server": ""}):
        mail = _mail(**overrides)
        assert not mail.is_configured
        with patch("smtplib.SMTP", FakeSMTP):
            assert notify(_bench(tmp_path, mail), PAYLOAD) is False
    assert FakeSMTP.sends == []


def test_mail_counts_as_delivery_when_every_webhook_fails(tmp_path: Path) -> None:
    bench = _bench(tmp_path, _mail(), webhook_endpoints={"https://one.test": "token"})

    with (
        patch("smtplib.SMTP", FakeSMTP),
        patch("pilot.core.alerts.send_alert", side_effect=OSError("unreachable")),
    ):
        delivered = notify(bench, PAYLOAD)

    assert delivered
    assert len(FakeSMTP.sends) == 1


def test_an_unreachable_mail_server_is_not_a_delivery(tmp_path: Path) -> None:
    with patch("smtplib.SMTP", side_effect=OSError("unreachable")):
        assert not notify(_bench(tmp_path, _mail()), PAYLOAD)


def test_no_mail_goes_out_without_recipients(tmp_path: Path) -> None:
    with patch("smtplib.SMTP", FakeSMTP):
        notify(_bench(tmp_path, _mail(), email_recipients=[]), PAYLOAD)

    assert FakeSMTP.sends == []


def test_recipients_may_be_saved_before_a_mailbox_exists() -> None:
    """Recipients are configured on the notifications page, the mailbox on its
    own one, so neither order of setting them up may fail validation."""
    ResourceLimitConfig(email_recipients=list(RECIPIENTS)).validate()
    _mail(server="", email="", password="").validate()


def test_a_bad_recipient_is_rejected() -> None:
    with pytest.raises(ValueError, match="bad address"):
        ResourceLimitConfig(email_recipients=["ops.test"]).validate()


def test_malformed_server_settings_are_rejected() -> None:
    with pytest.raises(ValueError, match="auto_email_id must be an email address"):
        _mail(email="alerts").validate()

    with pytest.raises(ValueError, match="mail_port must be a port number"):
        _mail(port=70000).validate()


def test_the_credential_check_opens_and_drops_a_session() -> None:
    """Settings are proved against the server while they are being saved, the
    way the framework's Email Account opens a session on save."""
    with patch("smtplib.SMTP", FakeSMTP):
        check_mail_credentials(_mail())

    sent = FakeSMTP.sends[0]
    assert sent.logged_in_as == ("alerts@test", "secret")
    assert sent.messages == []


def test_the_credential_check_raises_on_a_bad_password() -> None:
    error = smtplib.SMTPAuthenticationError(535, b"Bad credentials")
    with (
        patch("smtplib.SMTP", FakeSMTP),
        patch.object(FakeSMTP, "login", side_effect=error),
        pytest.raises(smtplib.SMTPAuthenticationError),
    ):
        check_mail_credentials(_mail())


def test_an_unparsable_mailbox_survives_an_unrelated_save(tmp_path: Path) -> None:
    """A settings save that never touched mail must not delete a mailbox it
    could not validate, and the framework allows auto_email_id to be absent."""
    stored = {
        "db_host": "127.0.0.1",
        "mail_server": "smtp.example.com",
        "mail_login": "alerts@test",
        "mail_password": "secret",
        "use_tls": 1,
    }
    (tmp_path / "common_site_config.json").write_text(json.dumps(stored))

    MailConfig.read(tmp_path).write(tmp_path)

    written = json.loads((tmp_path / "common_site_config.json").read_text())
    assert written["mail_password"] == "secret"
    assert written["db_host"] == "127.0.0.1"
    # The absent address is filled in from the login name rather than dropped.
    assert written["auto_email_id"] == "alerts@test"


def test_the_login_name_is_the_sender_when_no_address_is_stored(tmp_path: Path) -> None:
    """The framework reads auto_email_id, then falls back to mail_login."""
    (tmp_path / "common_site_config.json").write_text(
        json.dumps({"mail_server": "smtp.test", "mail_login": "alerts@test", "mail_password": "secret"})
    )

    assert MailConfig.read(tmp_path).email == "alerts@test"


def test_clearing_the_server_drops_the_stored_mailbox(tmp_path: Path) -> None:
    (tmp_path / "common_site_config.json").write_text(
        json.dumps({"db_host": "127.0.0.1", "mail_server": "smtp.test", "auto_email_id": "alerts@test"})
    )

    mail = MailConfig.read(tmp_path)
    mail.server = ""
    mail.write(tmp_path)

    assert json.loads((tmp_path / "common_site_config.json").read_text()) == {"db_host": "127.0.0.1"}


def test_a_damaged_site_config_is_not_overwritten(tmp_path: Path) -> None:
    """_load() would otherwise read a truncated file as empty, and the write
    would replace every unrelated setting with mail fields alone."""
    damaged = '{"db_host": "127.0.0.1", "redis_cache": trunc'
    (tmp_path / "common_site_config.json").write_text(damaged)

    with pytest.raises(MalformedSiteConfig):
        MailConfig(server="smtp.test", email="alerts@test", password="secret").write(tmp_path)

    assert (tmp_path / "common_site_config.json").read_text() == damaged


def test_a_damaged_site_config_reads_as_no_mailbox(tmp_path: Path) -> None:
    """The monitor tick reads this, so a damaged file must not raise into it."""
    (tmp_path / "common_site_config.json").write_text("{ truncated")

    assert MailConfig.read(tmp_path).is_configured is False


def test_alerts_skip_central_on_a_self_hosted_bench(tmp_path: Path) -> None:
    """Nothing to report to, and asking would read instance metadata per alert."""
    from unittest.mock import patch

    from pilot.core import alerts

    bench = _bench(tmp_path, MailConfig())
    bench.config.central.enabled = False

    with patch.object(alerts, "CentralClient") as client:
        alerts.notify(bench, {"event": "cpu", "message": "high", "context": {}})

    client.assert_not_called()


def test_alerts_reach_central_when_it_is_managed(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.core import alerts

    bench = _bench(tmp_path, MailConfig())
    bench.config.central.enabled = True

    with patch.object(alerts, "CentralClient") as client:
        assert alerts.notify(bench, {"event": "cpu", "message": "high", "context": {}}) is True

    client.return_value.notify_central.assert_called_once()
