"""Crypto module for AI Email Assistant.

Provides Fernet encryption for sensitive data storage.
"""

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import get_settings
from log_config import get_logger

logger = get_logger(__name__)


class CryptoManager:
    """Manage encryption/decryption using Fernet."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._fernet: Fernet | None = None
        self._init_encryption()

    def _init_encryption(self) -> None:
        """Initialize Fernet encryption from environment variable."""
        encryption_key = self.settings.encryption_key
        if encryption_key:
            try:
                key_str = encryption_key
                # Key should be 32 url-safe base64-encoded bytes
                key_bytes = key_str.encode()
                if len(key_bytes) != 32:
                    # Derive a proper 32-byte key using PBKDF2
                    salt = b"ai-email-assistant-salt"  # Fixed salt for deterministic key derivation
                    kdf = PBKDF2HMAC(
                        algorithm=hashes.SHA256(),
                        length=32,
                        salt=salt,
                        iterations=100000,
                    )
                    key_bytes = base64.urlsafe_b64encode(kdf.derive(key_bytes))
                self._fernet = Fernet(key_bytes)
                logger.info("Encryption initialized")
            except Exception as e:
                logger.warning("Failed to initialize encryption", error=str(e))
                self._fernet = None
        else:
            logger.warning("No encryption key configured, data will be stored in plaintext")

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key (base64 encoded)."""
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return base64 encoded ciphertext."""
        if self._fernet:
            return self._fernet.encrypt(plaintext.encode()).decode()
        return plaintext

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64 encoded ciphertext string."""
        if self._fernet:
            try:
                return self._fernet.decrypt(ciphertext.encode()).decode()
            except Exception as e:
                logger.error("Decryption failed", error=str(e))
                raise
        return ciphertext

    def is_encrypted(self) -> bool:
        """Check if encryption is enabled."""
        return self._fernet is not None


# Global instance
crypto_manager = CryptoManager()


# Convenience functions
def generate_key() -> str:
    """Generate a new Fernet key (base64 encoded)."""
    return CryptoManager.generate_key()


def encrypt(plaintext: str) -> str:
    """Encrypt a string using the global crypto manager."""
    return crypto_manager.encrypt(plaintext)


def decrypt(ciphertext: str) -> str:
    """Decrypt a string using the global crypto manager."""
    return crypto_manager.decrypt(ciphertext)
