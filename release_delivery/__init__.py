"""Optional TLS-only SMTP delivery; callbacks send only when invoked by auth."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import quote, urlsplit


class DeliveryError(RuntimeError):
    pass


def register(app):
    keys = ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TLS_MODE")
    values = [app.config.get(k) for k in keys]
    if not any(values):
        return
    if not all(values):
        raise ValueError("Complete SMTP host, port, credentials, sender and TLS mode configuration is required")
    host, port, username, password, sender, mode = values
    if (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535
            or mode not in {"starttls", "ssl"} or not isinstance(sender, str)
            or "\r" in sender or "\n" in sender or parseaddr(sender)[1] != sender or "@" not in sender):
        raise ValueError("SMTP requires a valid port, bare sender address and starttls or ssl")
    origin = app.config.get("PUBLIC_ORIGIN", "").rstrip("/")
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError("Configure the explicit server PUBLIC_ORIGIN before email delivery")

    def send(recipient, token, purpose):
        try:
            message = EmailMessage()
            message["From"], message["To"] = sender, recipient
            message["Subject"] = "OpenFotoFrame email verification" if purpose == "verify" else "OpenFotoFrame account recovery"
            link = origin + "/" + purpose + "#token=" + quote(token, safe="")
            message.set_content("Open this link on your trusted device:\n\n" + link +
                "\n\nIf you did not request this message, you can ignore it. Do not share this link.")
            context = ssl.create_default_context()
            if mode == "ssl":
                connection = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
            else:
                connection = smtplib.SMTP(host, port, timeout=15)
            with connection as smtp:
                smtp.ehlo()
                if mode == "starttls":
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(message)
        except Exception:
            # Never expose recipient, token, provider response or credentials.
            raise DeliveryError("Email delivery failed; contact the server operator.") from None

    app.config["VERIFICATION_DELIVERY"] = lambda recipient, token: send(recipient, token, "verify")
    app.config["RECOVERY_DELIVERY"] = lambda recipient, token: send(recipient, token, "recover")
