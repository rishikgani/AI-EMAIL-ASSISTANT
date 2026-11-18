"""
Attachment handling utilities for upload validation, base64 encoding, and thumbnail generation.
"""

import base64
import mimetypes
import os
from dataclasses import dataclass
from typing import BinaryIO

from email_tools import EmailAttachment


@dataclass
class AttachmentConfig:
    """Configuration for attachment handling."""
    max_size: int = 25 * 1024 * 1024  # 25MB
    allowed_types: list[str] = None

    def __post_init__(self):
        if self.allowed_types is None:
            self.allowed_types = [
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


def guess_content_type(filename: str) -> str:
    """Guess MIME type from filename."""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def validate_attachment(file_obj: BinaryIO, filename: str, config: AttachmentConfig | None = None) -> tuple[bool, str]:
    """Validate uploaded file."""
    if config is None:
        config = AttachmentConfig()

    # Check size
    file_obj.seek(0, os.SEEK_END)
    size = file_obj.tell()
    file_obj.seek(0)

    if size > config.max_size:
        return False, f"File exceeds maximum size of {config.max_size // (1024*1024)}MB"

    # Check content type
    content_type = guess_content_type(filename)
    if content_type not in config.allowed_types:
        return False, f"File type {content_type} not allowed. Allowed: {', '.join(config.allowed_types)}"

    return True, "OK"


def encode_to_base64(file_obj: BinaryIO) -> str:
    """Encode file content to base64 string."""
    file_obj.seek(0)
    data = file_obj.read()
    return base64.b64encode(data).decode('utf-8')


def process_uploaded_file(uploaded_file, config: AttachmentConfig | None = None) -> EmailAttachment:
    """
    Process a Streamlit uploaded file into an EmailAttachment.

    Args:
        uploaded_file: Streamlit UploadedFile object
        config: Optional AttachmentConfig for validation

    Returns:
        EmailAttachment ready for email sending

    Raises:
        ValueError: If validation fails
    """
    if config is None:
        config = AttachmentConfig()

    # Validate
    valid, message = validate_attachment(uploaded_file, uploaded_file.name, config)
    if not valid:
        raise ValueError(message)

    # Read and encode
    file_obj = uploaded_file
    content_type = guess_content_type(uploaded_file.name)
    size = file_obj.size
    data = encode_to_base64(file_obj)

    return EmailAttachment(
        filename=uploaded_file.name,
        content_type=content_type,
        data=data,
        size=size,
    )


def generate_thumbnail_base64(image_data: bytes, max_size: tuple[int, int] = (200, 200)) -> str | None:
    """Generate base64 thumbnail for image attachments."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_data))
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

        # Convert to RGB if needed (for JPEG)
        if img.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception:
        return None


def is_image_file(content_type: str) -> bool:
    """Check if content type is an image."""
    return content_type.startswith('image/')


def get_file_icon(content_type: str) -> str:
    """Get emoji icon for file type."""
    if content_type.startswith('image/'):
        return '🖼️'
    elif content_type == 'application/pdf':
        return '📄'
    elif content_type in ('application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'):
        return '📝'
    elif content_type in ('application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'):
        return '📊'
    elif content_type in ('application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'):
        return '📽️'
    elif content_type.startswith('text/'):
        return '📄'
    elif content_type in ('application/zip', 'application/x-rar-compressed'):
        return '📦'
    else:
        return '📎'
