"""
Email composition utilities for building MIME messages.
"""

import base64
import html
import re
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import bleach

from email_tools import EmailAttachment, EmailResult
from sanitize import sanitize_html as sanitize_html_util


def build_mime_message(
    result: EmailResult,
    from_address: str,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> MIMEMultipart:
    """Build a MIME message from EmailResult."""
    msg: MIMEMultipart = MIMEMultipart("mixed")
    msg["Subject"] = result.subject
    msg["From"] = from_address
    msg["To"] = ", ".join(result.to) if result.to else ""
    if result.cc:
        msg["Cc"] = ", ".join(result.cc)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    msg["Message-ID"] = make_msgid()
    msg["Date"] = formatdate(localtime=True)

    # Create alternative part for text/html
    alt = MIMEMultipart("alternative")

    # Plain text part
    text_part = MIMEText(result.body or "", "plain", "utf-8")
    alt.attach(text_part)

    # HTML part
    if result.body_html:
        # Sanitize HTML for security
        clean_html = sanitize_html_util(result.body_html)
        html_part = MIMEText(clean_html, "html", "utf-8")
        alt.attach(html_part)
    elif result.body:
        # Auto-generate HTML from plain text
        html_body = plain_text_to_html(result.body)
        html_part = MIMEText(html_body, "html", "utf-8")
        alt.attach(html_part)

    msg.attach(alt)

    # Add attachments
    if result.attachments:
        for att in result.attachments:
            attach_file(msg, att)

    return msg


def plain_text_to_html(text: str) -> str:
    """Convert plain text to simple HTML."""
    # Escape HTML special chars
    escaped = html.escape(text)

    # Convert URLs to links
    url_pattern = r'(https?://[^\s]+)'
    escaped = re.sub(url_pattern, r'<a href="\1">\1</a>', escaped)

    # Convert email addresses to mailto links
    email_pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
    escaped = re.sub(email_pattern, r'<a href="mailto:\1">\1</a>', escaped)

    # Convert line breaks to paragraphs
    paragraphs = escaped.split('\n\n')
    html_paragraphs = [f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs if p.strip()]

    return ''.join(html_paragraphs)


def html_to_plain_text(html_content: str) -> str:
    """Convert HTML to plain text."""
    # Remove scripts and styles
    clean = bleach.clean(html_content, tags=[], strip=True)
    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def attach_file(msg: MIMEMultipart, attachment: EmailAttachment) -> None:
    """Attach a file to the message."""
    try:
        data = base64.b64decode(attachment.data)
    except Exception:
        # Invalid base64, skip
        return

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
        part.add_header("Content-Disposition", f"inline; filename={attachment.filename}")
    msg.attach(part)


def validate_email_address(email: str) -> bool:
    """Validate email address format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def validate_attachment(attachment: EmailAttachment, max_size: int = 25 * 1024 * 1024) -> tuple[bool, str]:
    """Validate attachment."""
    if attachment.size > max_size:
        return False, f"Attachment {attachment.filename} exceeds maximum size of {max_size // (1024*1024)}MB"

    # Check content type
    allowed_types = [
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'text/plain',
        'text/csv',
        'image/jpeg',
        'image/png',
        'image/gif',
        'image/webp',
        'application/zip',
        'application/x-rar-compressed',
    ]
    if attachment.content_type not in allowed_types:
        return False, f"Attachment type {attachment.content_type} not allowed"

    # Validate base64
    try:
        base64.b64decode(attachment.data, validate=True)
    except Exception:
        return False, f"Attachment {attachment.filename} has invalid base64 encoding"

    return True, "OK"


def generate_message_id() -> str:
    """Generate a unique Message-ID."""
    return make_msgid()


def build_references(references: list[str], in_reply_to: str | None) -> str:
    """Build References header value."""
    refs = []
    if references:
        refs.extend(references)
    if in_reply_to and in_reply_to not in refs:
        refs.append(in_reply_to)
    return " ".join(refs)


def parse_addresses(address_string: str) -> list[str]:
    """Parse comma-separated email addresses."""
    if not address_string:
        return []
    addresses = []
    for part in address_string.split(','):
        addr = part.strip()
        if addr and validate_email_address(addr):
            addresses.append(addr)
    return addresses


def format_address_list(addresses: list[str]) -> str:
    """Format list of addresses for header."""
    return ", ".join(addresses)
