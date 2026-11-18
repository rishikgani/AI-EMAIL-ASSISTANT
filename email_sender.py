"""
Email sending module supporting SMTP, Gmail API, and Outlook/Graph API.
"""

import base64
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Any

import httpx

from auth import AuthDeliveryError
from config import load_settings
from email_tools import EmailAttachment


@dataclass
class SendResult:
    """Result of an email send operation."""
    success: bool
    message_id: str
    provider: str
    error: str | None = None
    raw_response: Any = None


class EmailSender:
    """Unified email sender supporting multiple backends."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self._last_send_time = 0.0
        self._send_count = 0
        self._rate_limit_window = 60.0  # 1 minute window
        self._max_per_window = 30  # Max sends per window

    def _check_rate_limit(self) -> None:
        """Enforce rate limiting."""
        now = time.time()
        if now - self._last_send_time > self._rate_limit_window:
            self._send_count = 0
            self._last_send_time = now
        if self._send_count >= self._max_per_window:
            wait = self._rate_limit_window - (now - self._last_send_time)
            raise AuthDeliveryError(f"Rate limit exceeded. Please wait {wait:.0f} seconds.")

    def send(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[EmailAttachment] | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        from_address: str | None = None,
    ) -> SendResult:
        """Send an email using the best available provider."""
        self._check_rate_limit()

        # Try Gmail API first if configured
        if self._has_gmail_credentials():
            try:
                return self._send_gmail_api(
                    to, subject, body_text, body_html, cc, bcc,
                    attachments, in_reply_to, references, from_address
                )
            except Exception:
                # Fall through to SMTP
                pass

        # Try Outlook/Graph API if configured
        if self._has_outlook_credentials():
            try:
                return self._send_outlook_api(
                    to, subject, body_text, body_html, cc, bcc,
                    attachments, in_reply_to, references, from_address
                )
            except Exception:
                # Fall through to SMTP
                pass

        # Fallback to SMTP
        return self._send_smtp(
            to, subject, body_text, body_html, cc, bcc,
            attachments, in_reply_to, references, from_address
        )

    def _has_gmail_credentials(self) -> bool:
        return bool(
            self.settings.get("GMAIL_CLIENT_ID")
            and self.settings.get("GMAIL_CLIENT_SECRET")
            and self.settings.get("GMAIL_REFRESH_TOKEN")
        )

    def _has_outlook_credentials(self) -> bool:
        return bool(
            self.settings.get("OUTLOOK_CLIENT_ID")
            and self.settings.get("OUTLOOK_CLIENT_SECRET")
            and self.settings.get("OUTLOOK_REFRESH_TOKEN")
        )

    def _send_smtp(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[EmailAttachment] | None,
        in_reply_to: str | None,
        references: list[str] | None,
        from_address: str | None,
    ) -> SendResult:
        """Send via SMTP."""
        host = self.settings.get("EMAIL_SMTP_HOST", "")
        username = self.settings.get("EMAIL_SMTP_USERNAME", "")
        password = self.settings.get("EMAIL_SMTP_PASSWORD", "")
        sender = from_address or self.settings.get("EMAIL_FROM_ADDRESS", username)

        if not host or not sender:
            raise AuthDeliveryError("SMTP not configured. Set EMAIL_SMTP_HOST and EMAIL_FROM_ADDRESS.")

        port = int(self.settings.get("EMAIL_SMTP_PORT", "587"))
        use_tls = self.settings.get("EMAIL_SMTP_TLS", "true").lower() == "true"

        # Build message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = " ".join(references)
        msg["Message-ID"] = make_msgid()
        msg["Date"] = formatdate(localtime=True)

        # Add text part
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        # Add HTML part if provided
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))

        # Add attachments
        if attachments:
            for att in attachments:
                self._attach_file(msg, att)

        # Prepare recipient list
        all_recipients = to + (cc or []) + (bcc or [])

        try:
            if use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(host, port, timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    if username and password:
                        server.login(username, password)
                    server.send_message(msg, from_addr=sender, to_addrs=all_recipients)
            else:
                with smtplib.SMTP(host, port, timeout=30) as server:
                    server.ehlo()
                    if username and password:
                        server.login(username, password)
                    server.send_message(msg, from_addr=sender, to_addrs=all_recipients)

            self._send_count += 1
            return SendResult(
                success=True,
                message_id=msg["Message-ID"],
                provider="smtp",
                raw_response=None,
            )
        except (OSError, smtplib.SMTPException) as e:
            raise AuthDeliveryError(f"SMTP send failed: {e}") from e

    def _send_gmail_api(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[EmailAttachment] | None,
        in_reply_to: str | None,
        references: list[str] | None,
        from_address: str | None,
    ) -> SendResult:
        """Send via Gmail API using OAuth2."""
        access_token = self._get_gmail_access_token()
        if not access_token:
            raise AuthDeliveryError("Failed to obtain Gmail access token")

        # Build raw message
        raw_message = self._build_mime_message(
            to, subject, body_text, body_html, cc, bcc,
            attachments, in_reply_to, references, from_address
        )

        # Send via Gmail API
        url = "https://www.googleapis.com/upload/gmail/v1/users/me/messages/send"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "message/rfc822",
        }
        response = httpx.post(url, headers=headers, content=raw_message, timeout=30.0)
        response.raise_for_status()

        data = response.json()
        message_id = data.get("id", "")

        self._send_count += 1
        return SendResult(
            success=True,
            message_id=message_id,
            provider="gmail_api",
            raw_response=data,
        )

    def _send_outlook_api(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[EmailAttachment] | None,
        in_reply_to: str | None,
        references: list[str] | None,
        from_address: str | None,
    ) -> SendResult:
        """Send via Microsoft Graph API."""
        access_token = self._get_outlook_access_token()
        if not access_token:
            raise AuthDeliveryError("Failed to obtain Outlook access token")

        # Build message payload
        message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if body_html else "Text",
                    "content": body_html or body_text,
                },
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
            },
            "saveToSentItems": "true",
        }

        if cc:
            message["message"]["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]
        if bcc:
            message["message"]["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc
            ]
        if in_reply_to:
            message["message"]["internetMessageHeaders"] = [
                {"name": "In-Reply-To", "value": in_reply_to}
            ]
        if references:
            message["message"]["internetMessageHeaders"].append(
                {"name": "References", "value": " ".join(references)}
            )

        # Handle attachments
        if attachments:
            message["message"]["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.filename,
                    "contentType": att.content_type,
                    "contentBytes": att.data,
                }
                for att in attachments
            ]

        url = "https://graph.microsoft.com/v1.0/me/sendMail"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        response = httpx.post(url, headers=headers, json=message, timeout=30.0)
        response.raise_for_status()

        self._send_count += 1
        return SendResult(
            success=True,
            message_id="outlook-" + str(int(time.time())),
            provider="outlook_api",
            raw_response=response.json(),
        )

    def _get_gmail_access_token(self) -> str | None:
        """Get Gmail API access token using refresh token."""
        client_id = self.settings.get("GMAIL_CLIENT_ID")
        client_secret = self.settings.get("GMAIL_CLIENT_SECRET")
        refresh_token = self.settings.get("GMAIL_REFRESH_TOKEN")

        if not all([client_id, client_secret, refresh_token]):
            return None

        try:
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json().get("access_token")
        except httpx.HTTPError:
            return None

    def _get_outlook_access_token(self) -> str | None:
        """Get Microsoft Graph API access token using refresh token."""
        client_id = self.settings.get("OUTLOOK_CLIENT_ID")
        client_secret = self.settings.get("OUTLOOK_CLIENT_SECRET")
        refresh_token = self.settings.get("OUTLOOK_REFRESH_TOKEN")

        if not all([client_id, client_secret, refresh_token]):
            return None

        try:
            response = httpx.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json().get("access_token")
        except httpx.HTTPError:
            return None

    def _build_mime_message(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        attachments: list[EmailAttachment] | None,
        in_reply_to: str | None,
        references: list[str] | None,
        from_address: str | None,
    ) -> bytes:
        """Build a raw MIME message."""
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = from_address or self.settings.get("EMAIL_FROM_ADDRESS", "")
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = " ".join(references)
        msg["Message-ID"] = make_msgid()
        msg["Date"] = formatdate(localtime=True)

        # Create alternative part for text/html
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            alt.attach(MIMEText(body_html, "html", "utf-8"))
        msg.attach(alt)

        # Add attachments
        if attachments:
            for att in attachments:
                self._attach_file(msg, att)

        return msg.as_bytes()

    def _attach_file(self, msg: MIMEMultipart, attachment: EmailAttachment) -> None:
        """Attach a file to the message."""
        data = base64.b64decode(attachment.data)
        if attachment.content_type.startswith("image/"):
            part = MIMEImage(data, name=attachment.filename)
        else:
            part = MIMEApplication(data, name=attachment.filename)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={attachment.filename}",
        )
        if attachment.inline_cid:
            part.add_header("Content-ID", f"<{attachment.inline_cid}>")
            part.add_header("Content-Disposition", "inline")
        msg.attach(part)


def send_email(
    to: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[EmailAttachment] | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    from_address: str | None = None,
) -> SendResult:
    """Convenience function to send an email."""
    sender = EmailSender()
    return sender.send(
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
        in_reply_to=in_reply_to,
        references=references,
        from_address=from_address,
    )
