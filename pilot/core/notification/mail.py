from __future__ import annotations

import contextlib
import smtplib
import ssl
import typing
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape as html_escape
from pathlib import Path

from pilot.config.mail import MailConfig
from pilot.internal.template import Template


class AlertMail:
    """Alert emails sent through the bench's outgoing mail settings."""

    # A server that hangs must not hold up the tick that writes the metrics log.
    timeout = 10
    logo_path = Path(__file__).parents[1] / "assets" / "pilot-logo.png"
    template = Template.from_path(Path(__file__).parents[1] / "templates" / "alert_mail.html.template")
    # Detail columns per event: (context key holding the rows, [(heading, row key), ...]).
    tables: typing.ClassVar[dict[str, tuple[str, list[tuple[str, str]]]]] = {
        "resource_limit_breached": (
            "breached_limits",
            [("Limit", "limit"), ("Threshold", "threshold"), ("Reading", "reading")],
        ),
        "site_down": (
            "sites",
            [("Site", "site"), ("Status", "status_code"), ("Response (ms)", "response_ms")],
        ),
    }

    def __init__(self, mail: MailConfig) -> None:
        self.mail = mail

    @contextlib.contextmanager
    def connect(self) -> typing.Iterator[smtplib.SMTP]:
        """A connected, encrypted, logged-in SMTP session."""
        endpoint = self.mail.get_endpoint()
        # smtplib's own default context does not verify the peer, so pass one that does:
        # an unverified hop would hand the password to whoever answers.
        context = ssl.create_default_context()
        server: smtplib.SMTP
        if endpoint.is_ssl:
            server = smtplib.SMTP_SSL(endpoint.host, endpoint.port, timeout=self.timeout, context=context)
        else:
            server = smtplib.SMTP(endpoint.host, endpoint.port, timeout=self.timeout)
        with server:
            if not endpoint.is_ssl:
                server.starttls(context=context)
            if endpoint.username:
                server.login(endpoint.username, self.mail.password)
            yield server

    def check_credentials(self) -> None:
        """Open a session and drop it, so bad settings fail while they are saved. Raises like send()."""
        with self.connect():
            pass

    def send(self, recipients: list[str], payload: dict[str, typing.Any]) -> None:
        """Email one alert to every recipient; raise if any is refused, since a partial send is a failure."""
        logo = self.logo_path.read_bytes() if self.logo_path.exists() else b""
        logo_cid = make_msgid()[1:-1] if logo else ""
        text, html = self.render(payload, logo_cid)

        message = EmailMessage()
        message["Subject"] = f"[Pilot] {payload.get('title') or payload['message']}"
        message["From"] = self.mail.get_endpoint().sender
        message["To"] = ", ".join(recipients)
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        html_part = message.get_body(("html",))
        if logo and html_part is not None:
            # Attach the logo to the HTML part so it shows as an inline image, not a download.
            html_part.add_related(logo, "image", "png", cid=f"<{logo_cid}>")

        with self.connect() as server:
            if refused := server.send_message(message):
                raise smtplib.SMTPRecipientsRefused(refused)

    @classmethod
    def render(cls, payload: dict[str, typing.Any], logo_cid: str = "") -> tuple[str, str]:
        """Plain-text and HTML bodies for one alert."""
        summary = payload["message"]
        context = payload["context"]
        bench = context.get("bench", "")
        time_sent = context.get("time", "")

        headings, rows = cls.get_table(payload)
        text = summary + "\n"
        if rows:
            text += "\n" + "\n".join("  " + " / ".join(cells) for cells in rows) + "\n"
        text += f"\nBench: {bench}\nTime: {time_sent}\n\nThanks,\nTeam Pilot\n"

        # Nothing here is trusted markup: a site or limit name with a bracket in it
        # would otherwise land in the body as tags.
        html = cls.template.render(
            summary=html_escape(summary),
            bench=html_escape(bench),
            time=html_escape(time_sent),
            logo_cid=logo_cid,
            headings=[html_escape(heading) for heading in headings],
            rows=[[html_escape(cell) for cell in row] for row in rows],
        )
        return text, html

    @classmethod
    def get_table(cls, payload: dict[str, typing.Any]) -> tuple[list[str], list[list[str]]]:
        """The alert's detail table as (headings, rows); empty for an event without one."""
        spec = cls.tables.get(payload.get("event", ""))
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
