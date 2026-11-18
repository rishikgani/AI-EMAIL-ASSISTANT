#!/usr/bin/env python3
"""Get Gmail OAuth2 tokens and update secrets.toml"""

import tomllib
from pathlib import Path

import tomli_w
from google_auth_oauthlib.flow import InstalledAppFlow


def main():
    # Run OAuth flow - opens browser for authorization
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify'
    ])

    print("Opening browser for Google OAuth authorization...")
    print("Please sign in with rishikganiv@gmail.com and grant permissions.")
    print()

    creds = flow.run_local_server(port=0, open_browser=True)

    # Load current secrets.toml
    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    # Update with Gmail API credentials
    secrets["GMAIL_CLIENT_ID"] = creds.client_id
    secrets["GMAIL_CLIENT_SECRET"] = creds.client_secret
    secrets["GMAIL_REFRESH_TOKEN"] = creds.refresh_token

    # Write back
    with open(secrets_path, "wb") as f:
        tomli_w.dump(secrets, f)

    print()
    print("Gmail API tokens saved to .streamlit/secrets.toml")
    print("(refresh token not printed for security)")


if __name__ == "__main__":
    main()
