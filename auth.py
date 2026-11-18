import hashlib
import hmac
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import httpx

from config import load_settings


class AuthDeliveryError(RuntimeError):
    """Raised when a verification code cannot be delivered."""


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"


def _load_settings() -> dict[str, str]:
    """Backward compatibility wrapper - loads settings from config."""
    return load_settings()


def normalize_identifier(method: str, identifier: str) -> str:
    value = identifier.strip()
    if method == "Email":
        value = value.casefold()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value
    if method == "Phone":
        if not PHONE_PATTERN.fullmatch(value):
            raise ValueError("Enter a phone number in international format, such as +14155552671.")
        return value
    raise ValueError("Choose email or phone sign-in.")


def create_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_verification_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def verify_code(code: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_verification_code(code, salt), expected_hash)


def mask_identifier(method: str, identifier: str) -> str:
    if method == "Email":
        local_part, domain = identifier.split("@", maxsplit=1)
        return f"{local_part[:1]}***@{domain}"
    return f"{identifier[:3]}{'*' * max(0, len(identifier) - 7)}{identifier[-4:]}"


def send_verification_code(method: str, identifier: str, code: str) -> None:
    message_body = f"Your AI Email Assistant verification code is {code}. It expires in 5 minutes."
    if method == "Email":
        _send_email_code(identifier, message_body)
    elif method == "Phone":
        _send_sms_code(identifier, message_body)
    else:
        raise ValueError("Choose email or phone sign-in.")


def _send_email_code(identifier: str, message_body: str) -> None:
    settings = _load_settings()
    host = settings.get("EMAIL_SMTP_HOST", "")
    username = settings.get("EMAIL_SMTP_USERNAME", "")
    password = settings.get("EMAIL_SMTP_PASSWORD", "")
    sender = settings.get("EMAIL_FROM_ADDRESS", username)
    placeholder_values = (
        "yourgmail@gmail.com",
        "paste-your-app-password-here",
    )
    if username in placeholder_values or password in placeholder_values or sender in placeholder_values:
        raise AuthDeliveryError(
            "Email sign-in is not configured. Replace the placeholder Gmail address and app password "
            "in .streamlit/secrets.toml, then restart Streamlit."
        )
    missing = [
        name
        for name, value in (
            ("EMAIL_SMTP_HOST", host),
            ("EMAIL_FROM_ADDRESS or EMAIL_SMTP_USERNAME", sender),
        )
        if not value
    ]
    if bool(username) != bool(password):
        missing.append("both EMAIL_SMTP_USERNAME and EMAIL_SMTP_PASSWORD")
    if missing:
        raise AuthDeliveryError(
            f"Email sign-in is not configured. Set {', '.join(missing)} in your environment, "
            f"or in {SECRETS_PATH.relative_to(Path(__file__).parent)}, then restart Streamlit."
        )
    try:
        port = int(settings.get("EMAIL_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            if username:
                server.login(username, password)
            message = EmailMessage()
            message["Subject"] = "Your AI Email Assistant sign-in code"
            message["From"] = sender
            message["To"] = identifier
            message.set_content(message_body)
            server.send_message(message)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        detail = str(error).strip()
        raise AuthDeliveryError(
            "Could not send the email code. Check your SMTP host, port, username, and Gmail app password "
            f"and try again. SMTP error: {detail or type(error).__name__}"
        ) from error


def _send_sms_code(identifier: str, message_body: str) -> None:
    settings = _load_settings()
    account_sid = settings.get("TWILIO_ACCOUNT_SID", "")
    auth_token = settings.get("TWILIO_AUTH_TOKEN", "")
    from_number = settings.get("TWILIO_FROM_NUMBER", "")
    if not account_sid or not auth_token or not from_number:
        raise AuthDeliveryError(
            "Phone sign-in is not configured. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER in your environment or "
            f"in {SECRETS_PATH.relative_to(Path(__file__).parent)}, then restart Streamlit."
        )
    url = f"https://api.twilio.com/2010-04-01/Accounts/{quote(account_sid, safe='')}/Messages.json"
    try:
        response = httpx.post(
            url,
            data={"To": identifier, "From": from_number, "Body": message_body},
            auth=(account_sid, auth_token),
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise AuthDeliveryError("Could not send the SMS code. Check your Twilio settings and try again.") from error
