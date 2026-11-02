"""Outgoing email. PITCHSIGHT_EMAIL=log (default) writes messages to the log, for development;
smtp sends them through PITCHSIGHT_SMTP_* (STARTTLS)."""
import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger("pitchsight.mail")
outbox: list[EmailMessage] = []  # last messages sent with the log backend (tests read this)


def send(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.EMAIL_FROM, to, subject
    msg.set_content(body)
    if config.EMAIL_BACKEND == "smtp":
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return
    outbox.append(msg)
    del outbox[:-50]
    log.warning("EMAIL (not sent: PITCHSIGHT_EMAIL=log) to %s: %s\n%s", to, subject, body)
