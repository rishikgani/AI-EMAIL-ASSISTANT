"""
Configuration management using Pydantic Settings.
Centralizes all environment variables and secrets.
"""

import tomllib
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def read_streamlit_secrets() -> dict[str, Any]:
    """Read settings from Streamlit Cloud secrets and the local secrets file."""
    import streamlit as st
    from streamlit.errors import StreamlitSecretNotFoundError

    secrets: dict[str, Any] = {}
    with suppress(StreamlitSecretNotFoundError):
        secrets.update(dict(st.secrets))

    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return secrets
    try:
        with secrets_path.open("rb") as f:
            secrets.update(tomllib.load(f))
    except tomllib.TOMLDecodeError:
        return secrets
    return secrets


class StreamlitSecretsSource(PydanticBaseSettingsSource):
    """Pydantic settings source for .streamlit/secrets.toml."""

    def get_field_value(self, field_name: str, field: Any) -> Any:
        secrets = read_streamlit_secrets()
        # Check for uppercase key (as used in secrets.toml)
        upper_key = field_name.upper()
        if upper_key in secrets:
            return secrets[upper_key]
        # Check for lowercase key
        if field_name in secrets:
            return secrets[field_name]
        return None

    def prepare_field_value(self, field_name: str, field: Any, value: Any, value_is_complex: bool) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        secrets = read_streamlit_secrets()
        # Convert all keys to lowercase for case-insensitive matching
        return {k.lower(): v for k, v in secrets.items()}


class Settings(BaseSettings):
    """Application settings loaded from environment, .env file, and .streamlit/secrets.toml."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: init > env > .env > .streamlit/secrets.toml
        return (init_settings, env_settings, dotenv_settings, StreamlitSecretsSource(settings_cls))

    # SMTP Configuration (for sending emails & email OTP sign-in)
    email_smtp_host: str = ""
    email_smtp_port: Annotated[int, Field(ge=1, le=65535)] = 587
    email_smtp_username: str = ""
    email_smtp_password: str = ""
    email_from_address: str = ""
    email_smtp_tls: bool = True

    # IMAP Configuration (for fetching inbox, searching, reading threads)
    email_imap_host: str = ""
    email_imap_port: Annotated[int, Field(ge=1, le=65535)] = 993
    email_imap_username: str = ""
    email_imap_password: str = ""

    # Gmail API (OAuth2) - alternative to IMAP
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""

    # Outlook/Graph API (OAuth2)
    outlook_client_id: str = ""
    outlook_client_secret: str = ""
    outlook_refresh_token: str = ""

    # SMS Delivery (Twilio) - for phone OTP sign-in
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Cloud LLM APIs (optional - can also enter in UI sidebar)
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Database
    email_assistant_db: str = str(Path(__file__).parent / "data" / "drafts.sqlite3")

    # Security
    encryption_key: str = ""  # Fernet key for encrypting sensitive data

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # Rate limiting
    rate_limit_window: int = 60
    rate_limit_max: int = 30

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return upper

    @property
    def has_smtp(self) -> bool:
        """Check if SMTP is configured for sending."""
        sender = self.email_from_address or self.email_smtp_username
        return bool(self.email_smtp_host and sender)

    @property
    def has_imap(self) -> bool:
        """Check if IMAP is configured for reading."""
        return bool(self.email_imap_host and self.email_imap_username and self.email_imap_password)

    @property
    def has_gmail_api(self) -> bool:
        """Check if Gmail API credentials are configured."""
        return bool(self.gmail_client_id and self.gmail_client_secret and self.gmail_refresh_token)

    @property
    def has_outlook_api(self) -> bool:
        """Check if Outlook API credentials are configured."""
        return bool(self.outlook_client_id and self.outlook_client_secret and self.outlook_refresh_token)

    @property
    def has_twilio(self) -> bool:
        """Check if Twilio is configured for SMS."""
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Backward compatibility function for existing code
def load_settings() -> dict[str, str]:
    """
    Load settings as a dictionary for backward compatibility.
    This mimics the old _load_settings() function behavior.
    """
    settings = get_settings()
    return {
        "EMAIL_SMTP_HOST": settings.email_smtp_host,
        "EMAIL_SMTP_PORT": str(settings.email_smtp_port),
        "EMAIL_SMTP_USERNAME": settings.email_smtp_username,
        "EMAIL_SMTP_PASSWORD": settings.email_smtp_password,
        "EMAIL_FROM_ADDRESS": settings.email_from_address,
        "EMAIL_SMTP_TLS": str(settings.email_smtp_tls).lower(),
        "EMAIL_IMAP_HOST": settings.email_imap_host,
        "EMAIL_IMAP_PORT": str(settings.email_imap_port),
        "EMAIL_IMAP_USERNAME": settings.email_imap_username,
        "EMAIL_IMAP_PASSWORD": settings.email_imap_password,
        "GMAIL_CLIENT_ID": settings.gmail_client_id,
        "GMAIL_CLIENT_SECRET": settings.gmail_client_secret,
        "GMAIL_REFRESH_TOKEN": settings.gmail_refresh_token,
        "OUTLOOK_CLIENT_ID": settings.outlook_client_id,
        "OUTLOOK_CLIENT_SECRET": settings.outlook_client_secret,
        "OUTLOOK_REFRESH_TOKEN": settings.outlook_refresh_token,
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
        "TWILIO_FROM_NUMBER": settings.twilio_from_number,
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "EMAIL_ASSISTANT_DB": settings.email_assistant_db,
        "ENCRYPTION_KEY": settings.encryption_key,
    }
