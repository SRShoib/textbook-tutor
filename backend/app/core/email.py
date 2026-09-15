"""
What: the one function anything that needs to email a user calls. Right
      now that's only the password-reset flow.
Why one function, two backends, instead of always sending real email: this
      project has no email service configured and none is being added for
      it (see the password-reset plan's judgment call -- a real third-party
      email provider is a real infrastructure commitment this thesis
      prototype doesn't need, and it would mean sending children's email
      addresses to yet another third party). "console" (the default) prints
      the link instead -- the whole real flow is testable locally with no
      credentials. "smtp" sends for real via the stdlib, no new dependency,
      for the day (if ever) this needs to reach a real inbox. Same "one
      seam" shape as pipeline/llm.py's single call_llm() function.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from app.core.config import get_settings


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    settings = get_settings()
    subject = "Reset your Textbook Tutor password"
    body = (
        "We received a request to reset your Textbook Tutor password.\n\n"
        f"Reset it here: {reset_link}\n\n"
        f"This link expires in {settings.password_reset_token_minutes} minutes. "
        "If you didn't ask for this, you can ignore this message."
    )

    if settings.email_backend == "console":
        print(f"[password reset] to={to_email} link={reset_link}")
        return

    message = MIMEText(body, "plain")
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to_email

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
