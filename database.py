"""Database module for AI Email Assistant.

Provides SQLite storage with encryption for sensitive fields
and file-based attachment storage.
"""

import base64
import json
import math
import os
import shutil
import sqlite3
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import get_settings
from crypto import crypto_manager
from log_config import get_logger

logger = get_logger(__name__)


@dataclass
class Draft:
    """Represents an email draft."""
    id: int | None
    owner_identifier: str
    subject: str
    body_text: str
    body_html: str | None
    original_request: str
    category: str
    reason: str
    attachments: list[dict[str, Any]]
    created_at: str
    updated_at: str
    sent_at: str | None = None
    message_id: str | None = None


@dataclass
class RateLimitRecord:
    """Represents a rate limit record."""
    identifier: str
    endpoint: str
    count: int
    window_start: str


class Database:
    """SQLite database with encryption for sensitive fields."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.settings = get_settings()
        if db_path is not None:
            self.db_path = db_path
        else:
            self.db_path = Path(self.settings.email_assistant_db)
        self.attachments_dir = self.db_path.parent / "attachments"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _transaction(self):
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database tables."""
        with self._transaction() as conn:
            # Drafts table with encrypted fields
            conn.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_identifier TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL,
                    body_text TEXT NOT NULL,
                    body_html TEXT,
                    original_request TEXT NOT NULL,
                    category TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    attachments TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    message_id TEXT,
                    encryption_version INTEGER DEFAULT 1
                )
            """)

            # Rate limits table for persistent rate limiting
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    identifier TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    window_start TEXT NOT NULL,
                    PRIMARY KEY (identifier, endpoint)
                )
            """)

            # CSRF tokens table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS csrf_tokens (
                    token TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)

            # OAuth tokens table for Google (Gmail)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS google_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # OAuth tokens table for Outlook (Microsoft Graph)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outlook_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Login challenges table (for auth rate limiting)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS login_challenges (
                    owner_identifier TEXT PRIMARY KEY,
                    method TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                )
            """)

            # Create indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_created_at ON drafts(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_category ON drafts(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON rate_limits(window_start)")

            # Run migrations
            self._run_migrations(conn)

        logger.info("Database initialized", path=str(self.db_path))

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Run database migrations."""
        # Migration: Add encryption_version column if not exists
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE drafts ADD COLUMN encryption_version INTEGER DEFAULT 1")

        # Migration: Add body_html column if not exists
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE drafts ADD COLUMN body_html TEXT")

        # Migration: Add attachments column if not exists
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE drafts ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")

        # Migration: Add sent_at column if not exists
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE drafts ADD COLUMN sent_at TEXT")

        # Migration: Add message_id column if not exists
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE drafts ADD COLUMN message_id TEXT")

        # Migration: Add owner_identifier column if not exists
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE drafts ADD COLUMN owner_identifier TEXT NOT NULL DEFAULT ''")

        # Migration: Check for plaintext drafts and encrypt them
        self._migrate_plaintext_drafts(conn)

    def _migrate_plaintext_drafts(self, conn: sqlite3.Connection) -> None:
        """Migrate existing plaintext drafts to encrypted format."""
        if not crypto_manager.is_encrypted():
            return

        cursor = conn.execute("SELECT id, subject, body_text, body_html, original_request, category, reason FROM drafts WHERE encryption_version = 0 OR encryption_version IS NULL")
        rows = cursor.fetchall()

        if rows:
            logger.info("Migrating plaintext drafts to encrypted format", count=len(rows))
            for row in rows:
                encrypted_subject = crypto_manager.encrypt(row["subject"])
                encrypted_body_text = crypto_manager.encrypt(row["body_text"])
                encrypted_body_html = crypto_manager.encrypt(row["body_html"]) if row["body_html"] else None
                encrypted_original_request = crypto_manager.encrypt(row["original_request"])
                encrypted_category = crypto_manager.encrypt(row["category"])
                encrypted_reason = crypto_manager.encrypt(row["reason"])

                conn.execute("""
                    UPDATE drafts SET
                        subject = ?, body_text = ?, body_html = ?,
                        original_request = ?, category = ?, reason = ?,
                        encryption_version = 1
                    WHERE id = ?
                """, (encrypted_subject, encrypted_body_text, encrypted_body_html,
                      encrypted_original_request, encrypted_category, encrypted_reason, row["id"]))

    def _encrypt_draft_fields(self, draft: Draft) -> dict[str, Any]:
        """Encrypt sensitive fields of a draft."""
        return {
            "subject": crypto_manager.encrypt(draft.subject),
            "body_text": crypto_manager.encrypt(draft.body_text),
            "body_html": crypto_manager.encrypt(draft.body_html) if draft.body_html else None,
            "original_request": crypto_manager.encrypt(draft.original_request),
            "category": crypto_manager.encrypt(draft.category),
            "reason": crypto_manager.encrypt(draft.reason),
        }

    def _decrypt_draft_fields(self, row: sqlite3.Row) -> dict[str, Any]:
        """Decrypt sensitive fields of a draft."""
        return {
            "subject": crypto_manager.decrypt(row["subject"]),
            "body_text": crypto_manager.decrypt(row["body_text"]),
            "body_html": crypto_manager.decrypt(row["body_html"]) if row["body_html"] else None,
            "original_request": crypto_manager.decrypt(row["original_request"]),
            "category": crypto_manager.decrypt(row["category"]),
            "reason": crypto_manager.decrypt(row["reason"]),
        }

    def create_draft(self, draft: Draft) -> int:
        """Create a new draft and return its ID."""
        encrypted = self._encrypt_draft_fields(draft)
        attachments_json = json.dumps(draft.attachments)

        with self._transaction() as conn:
            cursor = conn.execute("""
                INSERT INTO drafts (
                    owner_identifier, subject, body_text, body_html, original_request,
                    category, reason, attachments, created_at, updated_at,
                    encryption_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                draft.owner_identifier,
                encrypted["subject"], encrypted["body_text"], encrypted["body_html"],
                encrypted["original_request"], encrypted["category"], encrypted["reason"],
                attachments_json, draft.created_at, draft.updated_at
            ))
            draft_id = cursor.lastrowid

            # Save attachments as files
            if draft.attachments:
                self._save_attachments(draft_id, draft.attachments)

        logger.info("Draft created", draft_id=draft_id)
        return draft_id

    def get_draft(self, draft_id: int, owner_identifier: str | None = None) -> Draft | None:
        """Get a draft by ID, optionally filtering by owner."""
        with self._get_connection() as conn:
            if owner_identifier is not None:
                cursor = conn.execute("SELECT * FROM drafts WHERE id = ? AND owner_identifier = ?", (draft_id, owner_identifier))
            else:
                cursor = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,))
            row = cursor.fetchone()

        if not row:
            return None

        decrypted = self._decrypt_draft_fields(row)
        attachments = json.loads(row["attachments"]) if row["attachments"] else []

        return Draft(
            id=row["id"],
            owner_identifier=row["owner_identifier"],
            subject=decrypted["subject"],
            body_text=decrypted["body_text"],
            body_html=decrypted["body_html"],
            original_request=decrypted["original_request"],
            category=decrypted["category"],
            reason=decrypted["reason"],
            attachments=attachments,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sent_at=row["sent_at"],
            message_id=row["message_id"],
        )

    def list_drafts(self, limit: int = 50, offset: int = 0, owner_identifier: str | None = None) -> list[Draft]:
        """List drafts with pagination, optionally filtering by owner."""
        with self._get_connection() as conn:
            if owner_identifier is not None:
                cursor = conn.execute("""
                    SELECT * FROM drafts
                    WHERE owner_identifier = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """, (owner_identifier, limit, offset))
            else:
                cursor = conn.execute("""
                    SELECT * FROM drafts
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            rows = cursor.fetchall()

        drafts = []
        for row in rows:
            decrypted = self._decrypt_draft_fields(row)
            attachments = json.loads(row["attachments"]) if row["attachments"] else []
            drafts.append(Draft(
                id=row["id"],
                owner_identifier=row["owner_identifier"],
                subject=decrypted["subject"],
                body_text=decrypted["body_text"],
                body_html=decrypted["body_html"],
                original_request=decrypted["original_request"],
                category=decrypted["category"],
                reason=decrypted["reason"],
                attachments=attachments,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                sent_at=row["sent_at"],
                message_id=row["message_id"],
            ))
        return drafts

    def update_draft(self, draft: Draft) -> bool:
        """Update an existing draft."""
        if draft.id is None:
            return False

        encrypted = self._encrypt_draft_fields(draft)
        attachments_json = json.dumps(draft.attachments)

        with self._transaction() as conn:
            cursor = conn.execute("""
                UPDATE drafts SET
                    subject = ?, body_text = ?, body_html = ?,
                    original_request = ?, category = ?, reason = ?,
                    attachments = ?, updated_at = ?,
                    sent_at = ?, message_id = ?
                WHERE id = ? AND owner_identifier = ?
            """, (
                encrypted["subject"], encrypted["body_text"], encrypted["body_html"],
                encrypted["original_request"], encrypted["category"], encrypted["reason"],
                attachments_json, draft.updated_at,
                draft.sent_at, draft.message_id, draft.id, draft.owner_identifier
            ))

            if draft.attachments:
                self._save_attachments(draft.id, draft.attachments)

        return cursor.rowcount > 0

    def delete_draft(self, draft_id: int, owner_identifier: str | None = None) -> bool:
        """Delete a draft and its attachments."""
        with self._transaction() as conn:
            if owner_identifier is not None:
                cursor = conn.execute(
                    "DELETE FROM drafts WHERE id = ? AND owner_identifier = ?",
                    (draft_id, owner_identifier),
                )
            else:
                cursor = conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))

        # Delete attachment directory
        attachment_dir = self.attachments_dir / str(draft_id)
        if attachment_dir.exists():
            shutil.rmtree(attachment_dir)

        logger.info("Draft deleted", draft_id=draft_id)
        return cursor.rowcount > 0

    def _save_attachments(self, draft_id: int, attachments: list[dict[str, Any]]) -> None:
        """Save attachments as files with encrypted metadata."""
        draft_attachments_dir = self.attachments_dir / str(draft_id)
        draft_attachments_dir.mkdir(parents=True, exist_ok=True)

        for idx, att in enumerate(attachments):
            if "data" in att and "filename" in att:
                file_path = draft_attachments_dir / f"{idx}_{att['filename']}"
                try:
                    file_path.write_bytes(base64.b64decode(att["data"]))
                except (ValueError, TypeError):
                    file_path.write_bytes(att["data"].encode("utf-8"))

                # Update attachment with file path (metadata stays in DB)
                att["file_path"] = str(file_path)
                att.pop("data", None)  # Remove data from metadata

    def get_attachment(self, draft_id: int, attachment_index: int) -> bytes | None:
        """Get attachment data by draft ID and index."""
        draft = self.get_draft(draft_id)
        if not draft or attachment_index >= len(draft.attachments):
            return None

        att = draft.attachments[attachment_index]
        file_path = att.get("file_path")
        if file_path and Path(file_path).exists():
            return Path(file_path).read_bytes()
        return None

    # Rate limiting methods
    def check_rate_limit(self, identifier: str, endpoint: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """Check if request is within rate limit. Returns (allowed, remaining)."""
        now = datetime.utcnow()
        window_start = now.replace(second=0, microsecond=0).isoformat()

        with self._transaction() as conn:
            # Clean old windows
            conn.execute("""
                DELETE FROM rate_limits
                WHERE datetime(window_start) < datetime(?, '-' || ? || ' seconds')
            """, (now.isoformat(), window_seconds))

            # Get or create rate limit record
            cursor = conn.execute("""
                SELECT count FROM rate_limits
                WHERE identifier = ? AND endpoint = ? AND window_start = ?
            """, (identifier, endpoint, window_start))
            row = cursor.fetchone()

            if row:
                current_count = row["count"]
                if current_count >= max_requests:
                    return False, 0
                new_count = current_count + 1
                conn.execute("""
                    UPDATE rate_limits SET count = ? WHERE identifier = ? AND endpoint = ? AND window_start = ?
                """, (new_count, identifier, endpoint, window_start))
                return True, max_requests - new_count
            else:
                conn.execute("""
                    INSERT INTO rate_limits (identifier, endpoint, count, window_start)
                    VALUES (?, ?, 1, ?)
                """, (identifier, endpoint, window_start))
                return True, max_requests - 1

    def get_rate_limit_status(self, identifier: str, endpoint: str, window_seconds: int) -> dict[str, Any]:
        """Get current rate limit status."""
        now = datetime.utcnow()
        window_start = now.replace(second=0, microsecond=0).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT count FROM rate_limits
                WHERE identifier = ? AND endpoint = ? AND window_start = ?
            """, (identifier, endpoint, window_start))
            row = cursor.fetchone()

        current_count = row["count"] if row else 0
        return {
            "identifier": identifier,
            "endpoint": endpoint,
            "current_count": current_count,
            "window_start": window_start,
            "window_seconds": window_seconds,
        }

    # Login challenge methods (for auth)
    def create_login_challenge(
        self,
        owner_identifier: str,
        method: str,
        salt: str,
        code_hash: str,
        created_at: float,
        expires_at: float,
        resend_seconds: int,
    ) -> int:
        """Create or update a login challenge. Returns wait time if resend cooldown active."""
        datetime.utcnow()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT created_at FROM login_challenges WHERE owner_identifier = ?",
                (owner_identifier,),
            ).fetchone()
            if row is not None and created_at - row["created_at"] < resend_seconds:
                wait_seconds = math.ceil(resend_seconds - (created_at - row["created_at"]))
                return wait_seconds
            conn.execute(
                """
                    INSERT INTO login_challenges (
                        owner_identifier, method, salt, code_hash, created_at, expires_at, attempts
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(owner_identifier) DO UPDATE SET
                        method = excluded.method,
                        salt = excluded.salt,
                        code_hash = excluded.code_hash,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at,
                        attempts = 0
                    """,
                (owner_identifier, method, salt, code_hash, created_at, expires_at),
            )
        return 0

    def get_login_challenge(self, owner_identifier: str) -> dict[str, Any] | None:
        """Get a login challenge by identifier."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM login_challenges WHERE owner_identifier = ?",
                (owner_identifier,),
            ).fetchone()
        return dict(row) if row is not None else None

    def record_login_attempt(self, owner_identifier: str) -> int:
        """Record a login attempt and return the new attempt count."""
        with self._transaction() as conn:
            conn.execute(
                """
                    UPDATE login_challenges
                    SET attempts = attempts + 1
                    WHERE owner_identifier = ?
                    """,
                (owner_identifier,),
            )
            row = conn.execute(
                "SELECT attempts FROM login_challenges WHERE owner_identifier = ?",
                (owner_identifier,),
            ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def clear_login_challenge(self, owner_identifier: str, expected_hash: str | None = None) -> bool:
        """Clear a login challenge."""
        with self._transaction() as conn:
            if expected_hash is None:
                conn.execute(
                    "DELETE FROM login_challenges WHERE owner_identifier = ?",
                    (owner_identifier,),
                )
            else:
                cursor = conn.execute(
                    """
                        DELETE FROM login_challenges
                        WHERE owner_identifier = ? AND code_hash = ?
                        """,
                    (owner_identifier, expected_hash),
                )
                return cursor.rowcount == 1
            return True

    # CSRF token methods
    def create_csrf_token(self, session_id: str, ttl_seconds: int = 3600) -> str:
        """Create a new CSRF token for a session."""
        import secrets
        token = secrets.token_urlsafe(32)
        now = datetime.utcnow()
        expires_at = now.timestamp() + ttl_seconds
        expires_iso = datetime.fromtimestamp(expires_at).isoformat()

        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO csrf_tokens (token, session_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
            """, (token, session_id, now.isoformat(), expires_iso))

        # Clean expired tokens
        self._clean_expired_csrf_tokens()

        return token

    def validate_csrf_token(self, token: str, session_id: str) -> bool:
        """Validate a CSRF token."""
        now = datetime.utcnow().isoformat()

        with self._transaction() as conn:
            cursor = conn.execute("""
                SELECT token FROM csrf_tokens
                WHERE token = ? AND session_id = ? AND expires_at > ?
            """, (token, session_id, now))
            row = cursor.fetchone()

            if row:
                # Token is valid, delete it (one-time use)
                conn.execute("DELETE FROM csrf_tokens WHERE token = ?", (token,))
                return True

        return False

    def _clean_expired_csrf_tokens(self) -> None:
        """Clean up expired CSRF tokens."""
        now = datetime.utcnow().isoformat()
        with self._transaction() as conn:
            conn.execute("DELETE FROM csrf_tokens WHERE expires_at <= ?", (now,))

    # OAuth token methods for Google (Gmail)
    def create_google_tokens(self, access_token: str, refresh_token: str, expires_at: float) -> None:
        """Create or update Google OAuth tokens."""
        now = datetime.utcnow().isoformat()
        encrypted_access = crypto_manager.encrypt(access_token)
        encrypted_refresh = crypto_manager.encrypt(refresh_token)

        with self._transaction() as conn:
            conn.execute("DELETE FROM google_tokens")
            conn.execute("""
                INSERT INTO google_tokens (access_token, refresh_token, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (encrypted_access, encrypted_refresh, expires_at, now, now))

    def get_google_tokens(self) -> dict | None:
        """Get stored Google OAuth tokens."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM google_tokens LIMIT 1").fetchone()

        if not row:
            return None

        return {
            "access_token": crypto_manager.decrypt(row["access_token"]),
            "refresh_token": crypto_manager.decrypt(row["refresh_token"]),
            "expires_at": row["expires_at"],
        }

    def delete_google_tokens(self) -> bool:
        """Delete stored Google OAuth tokens."""
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM google_tokens")
        return cursor.rowcount > 0

    # OAuth token methods for Outlook (Microsoft Graph)
    def create_outlook_tokens(self, access_token: str, refresh_token: str, expires_at: float) -> None:
        """Create or update Outlook OAuth tokens."""
        now = datetime.utcnow().isoformat()
        encrypted_access = crypto_manager.encrypt(access_token)
        encrypted_refresh = crypto_manager.encrypt(refresh_token)

        with self._transaction() as conn:
            conn.execute("DELETE FROM outlook_tokens")
            conn.execute("""
                INSERT INTO outlook_tokens (access_token, refresh_token, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (encrypted_access, encrypted_refresh, expires_at, now, now))

    def get_outlook_tokens(self) -> dict | None:
        """Get stored Outlook OAuth tokens."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM outlook_tokens LIMIT 1").fetchone()

        if not row:
            return None

        return {
            "access_token": crypto_manager.decrypt(row["access_token"]),
            "refresh_token": crypto_manager.decrypt(row["refresh_token"]),
            "expires_at": row["expires_at"],
        }

    def delete_outlook_tokens(self) -> bool:
        """Delete stored Outlook OAuth tokens."""
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM outlook_tokens")
        return cursor.rowcount > 0


# Global instance
_database: Database | None = None
_test_db_path: Path | None = None


def set_test_database_path(path: Path) -> None:
    """Set a test database path (for testing only)."""
    global _test_db_path, _database
    _test_db_path = path
    _database = None  # Reset to force recreation


def get_database() -> Database:
    """Get or create the global database instance."""
    global _database
    if _database is None:
        _database = Database(_test_db_path) if _test_db_path is not None else Database()
    return _database


# Backward compatibility functions for existing tests

DATABASE_PATH = Path(
    os.environ.get("EMAIL_ASSISTANT_DB", str(Path(__file__).parent / "data" / "drafts.sqlite3"))
)


def initialize_database() -> None:
    """Initialize database (backward compatibility)."""
    get_database()
    # Tables are already created in Database.__init__


def save_draft(
    owner_identifier: str,
    subject: str,
    body: str,
    original_request: str,
    action: str,
    category: str = "",
    reason: str = "",
    attachments: list[dict[str, Any]] | None = None,
    body_html: str = "",
) -> int:
    """Save a draft (backward compatibility)."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    draft = Draft(
        id=None,
        owner_identifier=owner_identifier,
        subject=subject,
        body_text=body,
        body_html=body_html or None,
        original_request=original_request,
        category=category,
        reason=reason,
        attachments=attachments or [],
        created_at=now,
        updated_at=now,
    )
    db = get_database()
    return db.create_draft(draft)


def get_draft(draft_id: int, owner_identifier: str) -> dict | None:
    """Get a draft by ID (backward compatibility)."""
    db = get_database()
    draft = db.get_draft(draft_id, owner_identifier)
    if draft is None:
        return None
    return {
        "id": draft.id,
        "owner_identifier": draft.owner_identifier,
        "subject": draft.subject,
        "body": draft.body_text,
        "body_html": draft.body_html,
        "original_request": draft.original_request,
        "action": draft.reason if draft.reason in ("generate", "reply", "send", "categorize") else "generate",
        "category": draft.category,
        "reason": draft.reason,
        "created_at": draft.created_at,
    }


def list_drafts(owner_identifier: str, limit: int = 100) -> list[dict]:
    """List drafts (backward compatibility)."""
    db = get_database()
    drafts = db.list_drafts(limit=limit, owner_identifier=owner_identifier)
    return [
        {
            "id": d.id,
            "owner_identifier": d.owner_identifier,
            "subject": d.subject,
            "body": d.body_text,
            "body_html": d.body_html,
            "original_request": d.original_request,
            "action": d.reason if d.reason in ("generate", "reply", "send", "categorize") else "generate",
            "category": d.category,
            "reason": d.reason,
            "created_at": d.created_at,
        }
        for d in drafts
    ]


def count_drafts(owner_identifier: str) -> int:
    """Count drafts (backward compatibility)."""
    db = get_database()
    return len(db.list_drafts(limit=1000, owner_identifier=owner_identifier))


def create_login_challenge(
    owner_identifier: str,
    method: str,
    salt: str,
    code_hash: str,
    created_at: float,
    expires_at: float,
    resend_seconds: int,
) -> int:
    """Create login challenge (backward compatibility)."""
    db = get_database()
    return db.create_login_challenge(
        owner_identifier, method, salt, code_hash, created_at, expires_at, resend_seconds
    )


def get_login_challenge(owner_identifier: str) -> dict | None:
    """Get login challenge (backward compatibility)."""
    db = get_database()
    return db.get_login_challenge(owner_identifier)


def record_login_attempt(owner_identifier: str) -> int:
    """Record login attempt (backward compatibility)."""
    db = get_database()
    return db.record_login_attempt(owner_identifier)


def clear_login_challenge(owner_identifier: str, expected_hash: str | None = None) -> bool:
    """Clear login challenge (backward compatibility)."""
    db = get_database()
    return db.clear_login_challenge(owner_identifier, expected_hash)
