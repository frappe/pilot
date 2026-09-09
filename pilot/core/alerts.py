from __future__ import annotations

import contextlib
import json
import smtplib
import ssl
import time
import typing
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape as html_escape
from pathlib import Path

from pilot.config.mail import MailConfig
from pilot.integrations.central import CentralClient, CentralClientError
from pilot.internal.template import Template

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

# How long a condition must hold before it is worth telling anyone about.
ALERT_SUSTAINED_SECONDS = 300

# A sink that hangs must not hold up the tick that writes the metrics log.
ALERT_TIMEOUT_SECONDS = 10


def send_alert(endpoint: str, token: str, payload: dict[str, typing.Any]) -> None:
    """POST one alert to a webhook endpoint."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=ALERT_TIMEOUT_SECONDS):
        pass


@contextlib.contextmanager
def smtp_session(mail: MailConfig) -> typing.Iterator[smtplib.SMTP]:
    """A connected, encrypted, logged-in SMTP session for these settings."""
    endpoint = mail.get_endpoint()
    # smtplib's own default context does not verify the peer, so pass one that does:
    # an unverified hop would hand the password to whoever answers.
    context = ssl.create_default_context()
    server: smtplib.SMTP
    if endpoint.is_ssl:
        server = smtplib.SMTP_SSL(
            endpoint.host, endpoint.port, timeout=ALERT_TIMEOUT_SECONDS, context=context
        )
    else:
        server = smtplib.SMTP(endpoint.host, endpoint.port, timeout=ALERT_TIMEOUT_SECONDS)
    with server:
        if not endpoint.is_ssl:
            server.starttls(context=context)
        if endpoint.username:
            server.login(endpoint.username, mail.password)
        yield server


def check_mail_credentials(mail: MailConfig) -> None:
    """Open a session and drop it, so bad settings are reported while they are
    being saved rather than at the first alert. Raises like `send_mail` does."""
    with smtp_session(mail):
        pass


_LOGO_PATH = Path(__file__).parent / "assets" / "pilot-logo.png"
_TEMPLATE = Template.from_path(Path(__file__).parent / "templates" / "alert_mail.html.template")

# Detail columns per alert type: (context key holding the rows, [(heading, row key), ...]).
_ALERT_TABLES = {
    "resource_limit_breached": ("breached_limits", [("Limit", "limit"), ("Threshold", "threshold"), ("Reading", "reading")]),
    "site_down": ("sites", [("Site", "site"), ("Status", "status_code"), ("Response (ms)", "response_ms")]),
}


def _rows(payload: dict[str, typing.Any]) -> tuple[list[str], list[list[str]]]:
    """The detail table for this alert as (headings, rows of cells). Empty for
    an unrecognised event, which then renders as the summary line alone."""
    spec = _ALERT_TABLES.get(payload.get("event", ""))
    if not spec:
        return [], []
    key, columns = spec
    items = payload["context"].get(key, [])
    # Alerts carry structured rows (dicts); tolerate a bare list of names too.
    if items and not isinstance(items[0], dict):
        return [columns[0][0]], [[str(item)] for item in items]
    headings = [heading for heading, _ in columns]
    rows = [
        [str(item.get(field) if item.get(field) is not None else "—") for _, field in columns]
        for item in items
    ]
    return headings, rows


def _render_alert(payload: dict[str, typing.Any], logo_cid: str = "") -> tuple[str, str]:
    """Plain-text and HTML bodies for one alert. The markup lives in
    templates/alert_mail.html.template; only the values are filled in here."""
    summary = payload["message"]
    context = payload["context"]
    bench = context.get("bench", "")
    time_sent = context.get("time", "")

    headings, rows = _rows(payload)
    text = summary + "\n"
    if rows:
        text += "\n" + "\n".join("  " + " / ".join(cells) for cells in rows) + "\n"
    text += f"\nBench: {bench}\nTime: {time_sent}\n\nThanks,\nTeam Pilot\n"

    # Nothing here is trusted markup: a site or limit name with a bracket in it
    # would otherwise land in the body as tags.
    html = _TEMPLATE.render(
        summary=html_escape(summary),
        bench=html_escape(bench),
        time=html_escape(time_sent),
        logo_cid=logo_cid,
        headings=[html_escape(heading) for heading in headings],
        rows=[[html_escape(cell) for cell in row] for row in rows],
    )
    return text, html


def send_mail(mail: MailConfig, recipients: list[str], payload: dict[str, typing.Any]) -> None:
    """Email one alert to every configured recipient.

    Raises if any recipient is refused: a partial send still leaves someone
    uninformed, and the caller retires the alert once this returns.
    """
    logo = _LOGO_PATH.read_bytes() if _LOGO_PATH.exists() else b""
    logo_cid = make_msgid()[1:-1] if logo else ""
    text, html = _render_alert(payload, logo_cid)

    message = EmailMessage()
    message["Subject"] = f"[Pilot] {payload['message']}"
    message["From"] = mail.get_endpoint().sender
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    html_part = message.get_body(("html",))
    if logo and html_part is not None:
        # Attach the logo to the HTML part so it shows as an inline image, not a download.
        html_part.add_related(logo, "image", "png", cid=f"<{logo_cid}>")

    with smtp_session(mail) as server:
        refused = server.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def notify(bench: "Bench", payload: dict[str, typing.Any]) -> bool:
    """If we made it to any of the webhooks or mailboxes we will mark this as a successful delivery."""
    delivered = False
    limits = bench.config.resource_limits

    for endpoint, token in limits.webhook_endpoints.items():
        try:
            send_alert(endpoint, token, payload)
        except OSError:
            continue
        delivered = True

    mail = MailConfig.read(bench.sites_path)
    if mail.is_configured and limits.email_recipients:
        try:
            send_mail(mail, limits.email_recipients, payload)
        except OSError:
            pass
        else:
            delivered = True

    try:
        CentralClient(bench).notify_central(**payload)
    except CentralClientError:
        return delivered
    return True


class SustainedAlerts:
    """Names the conditions that have held long enough to alert on. The file is
    the only record of how long each has been true, so one that recovers is
    dropped from it and the file goes away once nothing is active."""

    def __init__(self, path: Path, window_seconds: int = ALERT_SUSTAINED_SECONDS) -> None:
        self.path = path
        self.window_seconds = window_seconds

    def due(self, active: list[str]) -> list[str]:
        """Record this round and return the names that have crossed the window.
        They stay due until mark_notified() confirms an alert actually went out,
        so a round where every sink was down is retried on the next tick."""
        previous = self._read()
        now = time.time()

        state = {}
        due = []
        for name in sorted(active):
            entry = previous.get(name) or {"since": now, "notified": False}
            if not entry["notified"] and now - entry["since"] >= self.window_seconds:
                due.append(name)
            state[name] = entry

        self._write(state)
        return due

    def mark_notified(self, names: list[str]) -> None:
        """Call only once an alert has been delivered. A name marked here does
        not alert again until it recovers and breaks a second time."""
        state = self._read()
        for name in names:
            if name in state:
                state[name]["notified"] = True
        self._write(state)

    def sustained(self) -> list[str]:
        """Every condition that has held for the full window, read from the state
        `due()` just wrote - so call it after `due()`, not instead of it.

        Unlike `due()` this ignores delivery entirely. A condition an external sink
        already accepted is still sustained, and still needs a local record if the
        bench never managed to write one."""
        now = time.time()
        return sorted(
            name for name, entry in self._read().items() if now - entry["since"] >= self.window_seconds
        )

    def unrecorded(self, names: list[str]) -> list[str]:
        """Of `names`, the ones the bench has not written to its own feed yet.

        Delivery and recording are different facts. A due condition stays due until
        some sink accepts it, so the delivery attempt repeats every tick - the local
        record must not, or a bench with no reachable sink writes one row per tick
        for as long as the condition holds."""
        state = self._read()
        return [name for name in names if not state.get(name, {}).get("recorded")]

    def mark_recorded(self, names: list[str]) -> None:
        """Call once the bench's own feed holds a record for these conditions."""
        state = self._read()
        for name in names:
            if name in state:
                state[name]["recorded"] = True
        self._write(state)

    def _read(self) -> dict[str, dict]:
        """A hand-edited or truncated file starts the window over rather than
        stopping the daemon."""
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, state: dict[str, dict]) -> None:
        if not state:
            self.path.unlink(missing_ok=True)
            return
        self.path.write_text(json.dumps(state))
