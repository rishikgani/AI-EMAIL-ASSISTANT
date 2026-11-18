"""OAuth Wizard module for AI Email Assistant.

Provides UI for connecting Gmail and Outlook accounts via OAuth2.
"""

import json
import secrets
import urllib.parse
from datetime import datetime
from typing import Optional

import httpx
import streamlit as st

from config import get_settings
from crypto import crypto_manager
from database import get_database


def get_oauth_state() -> str:
    """Generate a secure OAuth state parameter."""
    return secrets.token_urlsafe(32)


def build_google_auth_url(state: str, redirect_uri: str) -> str:
    """Build Google OAuth2 authorization URL."""
    settings = get_settings()
    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join([
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        ]),
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def build_outlook_auth_url(state: str, redirect_uri: str) -> str:
    """Build Microsoft OAuth2 authorization URL."""
    settings = get_settings()
    params = {
        "client_id": settings.outlook_client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join([
            "https://graph.microsoft.com/Mail.Read",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Mail.ReadWrite",
            "offline_access",
        ]),
        "response_type": "code",
        "state": state,
    }
    return f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"


async def exchange_google_code(code: str, redirect_uri: str) -> dict | None:
    """Exchange Google authorization code for tokens."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30.0,
        )
    if response.status_code == 200:
        return response.json()
    return None


async def exchange_outlook_code(code: str, redirect_uri: str) -> dict | None:
    """Exchange Microsoft authorization code for tokens."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": settings.outlook_client_id,
                "client_secret": settings.outlook_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30.0,
        )
    if response.status_code == 200:
        return response.json()
    return None


async def refresh_google_token(refresh_token: str) -> dict | None:
    """Refresh Google access token."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.gmail_client_id,
                "client_secret": settings.gmail_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )
    if response.status_code == 200:
        return response.json()
    return None


async def refresh_outlook_token(refresh_token: str) -> dict | None:
    """Refresh Microsoft access token."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": settings.outlook_client_id,
                "client_secret": settings.outlook_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30.0,
        )
    if response.status_code == 200:
        return response.json()
    return None


def store_google_tokens(access_token: str, refresh_token: str, expires_in: int) -> None:
    """Store Google OAuth tokens encrypted in database."""
    db = get_database()
    now = datetime.utcnow()
    expires_at = now.timestamp() + expires_in
    db.create_google_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def store_outlook_tokens(access_token: str, refresh_token: str, expires_in: int) -> None:
    """Store Outlook OAuth tokens encrypted in database."""
    db = get_database()
    now = datetime.utcnow()
    expires_at = now.timestamp() + expires_in
    db.create_outlook_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


def get_stored_google_tokens() -> dict | None:
    """Get stored Google OAuth tokens."""
    db = get_database()
    return db.get_google_tokens()


def get_stored_outlook_tokens() -> dict | None:
    """Get stored Outlook OAuth tokens."""
    db = get_database()
    return db.get_outlook_tokens()


def render_oauth_wizard() -> None:
    """Render the OAuth Wizard page for connecting email accounts."""
    st.title("🔐 Connect Email Accounts")
    st.caption("Link your Gmail or Outlook account using OAuth2 for secure API access.")

    settings = get_settings()
    db = get_database()

    # Check existing connections
    google_tokens = db.get_google_tokens()
    outlook_tokens = db.get_outlook_tokens()

    google_connected = google_tokens is not None
    outlook_connected = outlook_tokens is not None

    # Status cards
    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.subheader("📧 Gmail")
            if google_connected:
                st.success("✅ Connected")
                if google_tokens.get("expires_at"):
                    expires = datetime.fromtimestamp(google_tokens["expires_at"])
                    st.caption(f"Token expires: {expires.strftime('%Y-%m-%d %H:%M UTC')}")
                if st.button("Disconnect Gmail", key="disconnect_gmail"):
                    db.delete_google_tokens()
                    st.success("Gmail disconnected")
                    st.rerun()
            else:
                st.info("Not connected")
                st.caption("Requires Gmail API credentials in settings")

    with col2:
        with st.container(border=True):
            st.subheader("📬 Outlook / Microsoft 365")
            if outlook_connected:
                st.success("✅ Connected")
                if outlook_tokens.get("expires_at"):
                    expires = datetime.fromtimestamp(outlook_tokens["expires_at"])
                    st.caption(f"Token expires: {expires.strftime('%Y-%m-%d %H:%M UTC')}")
                if st.button("Disconnect Outlook", key="disconnect_outlook"):
                    db.delete_outlook_tokens()
                    st.success("Outlook disconnected")
                    st.rerun()
            else:
                st.info("Not connected")
                st.caption("Requires Outlook API credentials in settings")

    st.divider()

    # OAuth Configuration Check
    gmail_configured = bool(settings.gmail_client_id and settings.gmail_client_secret)
    outlook_configured = bool(settings.outlook_client_id and settings.outlook_client_secret)

    if not gmail_configured and not outlook_configured:
        st.warning(
            "⚠️ No OAuth credentials configured. "
            "Add Gmail and/or Outlook API credentials to `.streamlit/secrets.toml` "
            "or environment variables to enable connections."
        )
        with st.expander("Required Credentials"):
            st.code("""
# Gmail API (Google Cloud Console)
GMAIL_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
GMAIL_CLIENT_SECRET = "your-client-secret"
# GMAIL_REFRESH_TOKEN will be generated after first OAuth flow

# Outlook API (Azure Portal)
OUTLOOK_CLIENT_ID = "your-azure-app-client-id"
OUTLOOK_CLIENT_SECRET = "your-azure-app-secret"
# OUTLOOK_REFRESH_TOKEN will be generated after first OAuth flow
""")
        return

    # Redirect URI (use current origin)
    redirect_uri = st.session_state.get("oauth_redirect_uri", "http://localhost:8502/oauth/callback")

    # Connect Gmail
    if gmail_configured and not google_connected:
        st.subheader("📧 Connect Gmail")
        if st.button("Connect Gmail Account", type="primary", key="connect_gmail"):
            state = get_oauth_state()
            st.session_state["oauth_state"] = state
            st.session_state["oauth_provider"] = "google"
            auth_url = build_google_auth_url(state, redirect_uri)
            st.link_button("Authorize with Google", auth_url, type="primary")
            st.info("Click the button above to authorize. You'll be redirected back here.")

    # Connect Outlook
    if outlook_configured and not outlook_connected:
        st.subheader("📬 Connect Outlook")
        if st.button("Connect Outlook Account", type="primary", key="connect_outlook"):
            state = get_oauth_state()
            st.session_state["oauth_state"] = state
            st.session_state["oauth_provider"] = "outlook"
            auth_url = build_outlook_auth_url(state, redirect_uri)
            st.link_button("Authorize with Microsoft", auth_url, type="primary")
            st.info("Click the button above to authorize. You'll be redirected back here.")

    # Handle OAuth callback
    if "oauth_callback" in st.session_state:
        handle_oauth_callback()


def handle_oauth_callback() -> None:
    """Handle OAuth callback and exchange code for tokens."""
    callback_data = st.session_state.pop("oauth_callback")
    code = callback_data.get("code")
    state = callback_data.get("state")
    provider = callback_data.get("provider")

    # Verify state
    expected_state = st.session_state.pop("oauth_state", None)
    if state != expected_state:
        st.error("❌ Invalid OAuth state. Please try again.")
        return

    if not code:
        error = callback_data.get("error", "Unknown error")
        st.error(f"❌ OAuth authorization failed: {error}")
        return

    with st.spinner(f"Exchanging {provider} authorization code for tokens..."):
        if provider == "google":
            import asyncio
            tokens = asyncio.run(exchange_google_code(code, st.session_state.get("oauth_redirect_uri", "http://localhost:8502/oauth/callback")))
            if tokens:
                store_google_tokens(
                    access_token=tokens.get("access_token", ""),
                    refresh_token=tokens.get("refresh_token", ""),
                    expires_in=tokens.get("expires_in", 3600),
                )
                st.success("✅ Gmail connected successfully!")
                st.rerun()
            else:
                st.error("❌ Failed to exchange Google authorization code")
        elif provider == "outlook":
            import asyncio
            tokens = asyncio.run(exchange_outlook_code(code, st.session_state.get("oauth_redirect_uri", "http://localhost:8502/oauth/callback")))
            if tokens:
                store_outlook_tokens(
                    access_token=tokens.get("access_token", ""),
                    refresh_token=tokens.get("refresh_token", ""),
                    expires_in=tokens.get("expires_in", 3600),
                )
                st.success("✅ Outlook connected successfully!")
                st.rerun()
            else:
                st.error("❌ Failed to exchange Outlook authorization code")


def init_oauth_callback_handling() -> None:
    """Initialize OAuth callback handling from URL parameters."""
    # Check for OAuth callback parameters in URL
    query_params = st.query_params
    if "code" in query_params and "state" in query_params:
        provider = query_params.get("provider", ["google"])[0] if isinstance(query_params.get("provider"), list) else query_params.get("provider", "google")
        st.session_state["oauth_callback"] = {
            "code": query_params["code"],
            "state": query_params["state"],
            "provider": provider,
        }
        # Clear URL parameters
        st.query_params.clear()