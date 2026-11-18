"""Session management module for AI Email Assistant.

Provides CSRF protection and session handling for web interfaces.
"""

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from database import get_database
from log_config import get_logger

logger = get_logger(__name__)


@dataclass
class Session:
    """Represents a user session."""
    session_id: str
    created_at: float
    data: dict[str, Any] = field(default_factory=dict)
    csrf_token: str | None = None
    csrf_token_expires: float = 0


class SessionManager:
    """Manage user sessions with CSRF protection."""

    def __init__(self, session_ttl: int = 3600, csrf_ttl: int = 3600) -> None:
        self.session_ttl = session_ttl
        self.csrf_ttl = csrf_ttl
        self._sessions: dict[str, Session] = {}

    def create_session(self) -> Session:
        """Create a new session."""
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        session = Session(
            session_id=session_id,
            created_at=now,
        )
        self._sessions[session_id] = session
        logger.debug("Session created", session_id=session_id[:8])
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID, checking expiration."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        # Check if session expired
        if time.time() - session.created_at > self.session_ttl:
            self.delete_session(session_id)
            return None

        return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.debug("Session deleted", session_id=session_id[:8])
            return True
        return False

    def get_or_create_csrf_token(self, session_id: str) -> str:
        """Get or create a CSRF token for a session."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Invalid session")

        now = time.time()
        # Check if existing token is still valid
        if session.csrf_token and session.csrf_token_expires > now:
            return session.csrf_token

        # Create new token and store in database
        db = get_database()
        token = db.create_csrf_token(session_id, self.csrf_ttl)
        session.csrf_token = token
        session.csrf_token_expires = now + self.csrf_ttl
        return token

    def validate_csrf_token(self, session_id: str, token: str) -> bool:
        """Validate a CSRF token for a session."""
        session = self.get_session(session_id)
        if not session:
            return False

        # Check against database (single-use tokens)
        db = get_database()
        return db.validate_csrf_token(token, session_id)

    def rotate_csrf_token(self, session_id: str) -> str:
        """Rotate the CSRF token for a session."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Invalid session")

        db = get_database()
        token = db.create_csrf_token(session_id, self.csrf_ttl)
        session.csrf_token = token
        session.csrf_token_expires = time.time() + self.csrf_ttl
        return token

    def cleanup_expired(self) -> int:
        """Clean up expired sessions."""
        now = time.time()
        expired = [
            sid for sid, session in self._sessions.items()
            if now - session.created_at > self.session_ttl
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.debug("Cleaned up expired sessions", count=len(expired))
        return len(expired)


# Global instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get or create the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


# Convenience functions for web frameworks
def create_session() -> Session:
    """Create a new session."""
    return get_session_manager().create_session()


def get_session(session_id: str) -> Session | None:
    """Get a session by ID."""
    return get_session_manager().get_session(session_id)


def validate_csrf(session_id: str, token: str) -> bool:
    """Validate a CSRF token."""
    return get_session_manager().validate_csrf_token(session_id, token)


def get_csrf_token(session_id: str) -> str:
    """Get or create a CSRF token for a session."""
    return get_session_manager().get_or_create_csrf_token(session_id)
