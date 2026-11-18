"""HTML sanitization module for AI Email Assistant.

Provides safe HTML sanitization using bleach.
"""

import bleach
from log_config import get_logger

logger = get_logger(__name__)

# Allowed HTML tags for email content
ALLOWED_TAGS = [
    "p", "b", "i", "u", "strong", "em", "a", "ul", "ol", "li",
    "br", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "hr", "table", "thead", "tbody",
    "tr", "th", "td", "img"
]

# Allowed attributes for each tag
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "span": ["style", "class"],
    "div": ["style", "class"],
    "p": ["style", "class"],
    "blockquote": ["style", "class"],
    "pre": ["style", "class"],
    "code": ["style", "class"],
    "table": ["style", "class"],
    "th": ["style", "class", "scope"],
    "td": ["style", "class"],
    "tr": ["style", "class"],
    "ul": ["style", "class"],
    "ol": ["style", "class"],
    "li": ["style", "class"],
    "h1": ["style", "class"],
    "h2": ["style", "class"],
    "h3": ["style", "class"],
    "h4": ["style", "class"],
    "h5": ["style", "class"],
    "h6": ["style", "class"],
    "hr": ["style", "class"],
}

# Allowed CSS styles (safe subset)
ALLOWED_STYLES = [
    "color", "background-color", "font-size", "font-weight", "font-style",
    "text-decoration", "text-align", "margin", "padding", "border",
    "width", "height", "max-width", "max-height", "display",
    "font-family", "line-height", "letter-spacing", "white-space",
]

# Allowed protocols for links
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "tel"]


def sanitize_html(html: str, *, strict: bool = False) -> str:
    """Sanitize HTML to prevent XSS attacks.

    Args:
        html: The HTML string to sanitize
        strict: If True, only allow basic formatting tags (no tables, images, etc.)

    Returns:
        Sanitized HTML string safe for display
    """
    if not html:
        return ""

    if strict:
        # Strict mode: only basic text formatting
        tags = ["p", "b", "i", "u", "strong", "em", "br", "span"]
        attributes = {"span": ["style"]}
        styles = ["color", "font-weight", "font-style", "text-decoration"]
    else:
        tags = ALLOWED_TAGS
        attributes = ALLOWED_ATTRIBUTES
        styles = ALLOWED_STYLES

    try:
        cleaned = bleach.clean(
            html,
            tags=tags,
            attributes=attributes,
            styles=styles,
            protocols=ALLOWED_PROTOCOLS,
            strip=True,  # Remove disallowed tags entirely
            strip_comments=True,
        )
        return cleaned
    except Exception as e:
        logger.error("HTML sanitization failed", error=str(e))
        # Return empty string on failure to be safe
        return ""


def sanitize_text(text: str) -> str:
    """Sanitize plain text (escape HTML entities).

    Args:
        text: The text to sanitize

    Returns:
        Text with HTML entities escaped
    """
    if not text:
        return ""
    return bleach.clean(text, tags=[], attributes={}, strip=True)


def linkify_text(text: str) -> str:
    """Convert URLs in plain text to clickable links.

    Args:
        text: Plain text that may contain URLs

    Returns:
        HTML with URLs converted to links
    """
    if not text:
        return ""
    return bleach.linkify(
        bleach.clean(text, tags=[], attributes={}, strip=True),
        parse_email=True,
        callbacks=[_linkify_callback],
    )


def _linkify_callback(attrs, new=False):
    """Callback to add security attributes to links."""
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs
