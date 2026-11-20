# AI Email Assistant Agent

A Streamlit application that identifies an email task, calls either a local Ollama model or a cloud model through LangChain, and saves editable drafts in SQLite. It can generate a new email, suggest a reply, or categorize a message as Work, Personal, or Urgent. It never sends email.

## Requirements

- Python 3.11 or newer
- [Ollama](https://ollama.com/) installed and a model downloaded, for example `llama3.2` (the default, no paid API needed)
- Optional cloud AI: an OpenAI or Anthropic API key

## Run on Windows

From this project folder, run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ollama pull llama3.2
streamlit run app.py
```

If PowerShell prevents environment activation, run the venv's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The sidebar defaults to **Ollama (Local)** and uses the model you pulled above. Ollama must be running at `http://localhost:11434`. OpenAI and Anthropic are optional cloud providers and require their own API keys and may incur charges. Drafts are stored in `data/drafts.sqlite3`; set `EMAIL_ASSISTANT_DB` to use another SQLite file.

The app's dark navy and teal theme is configured in `.streamlit/config.toml`. Its logo artwork is in `assets/`.
Use the sidebar navigation to switch between the email assistant and the host dashboard. The dashboard reports Ollama availability, installed models, app runtime details, and SQLite draft storage.

## Deploy to Streamlit Community Cloud

1. Push this project to a GitHub repository. Do not commit `.streamlit/secrets.toml`, `.env`, or `data/`.
2. Open Streamlit Community Cloud and choose **New app**.
3. Select the repository, branch, and `app.py` as the main file.
4. Open **Advanced settings -> Secrets** and add the values from `.streamlit/secrets.toml.example`.
5. Generate an encryption key with:

   ```powershell
   .\.venv\Scripts\python.exe -c "from crypto import generate_key; print(generate_key())"
   ```

   Add the output as `ENCRYPTION_KEY` in the Cloud secrets.
6. Configure `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in Streamlit Community Cloud secrets to use a cloud model. Streamlit Community Cloud cannot connect to Ollama running on your personal computer, so cloud inference requires a provider account and may incur charges.
7. Configure Gmail SMTP or another SMTP provider for sign-in and email sending.
8. Deploy the app.

### Cloud limitations

- Community Cloud does not expose your local Ollama server, so select OpenAI or Anthropic for a Cloud deployment.
- SQLite storage and uploaded attachments are not durable across every Community Cloud restart or redeployment.
- Use a hosted database and object storage before serving a public, production user base.
- Enable HTTPS and domain-level abuse controls through the hosting platform and provider accounts.

## Sign-in setup

The app requires a one-time code sent to the user's email or phone. Configure the matching delivery provider before starting Streamlit; do not put credentials in the source code or commit them. The ignored `.streamlit/secrets.toml` file can hold credentials locally; environment variables override matching file settings.
Without provider settings, the app intentionally blocks sign-in rather than accepting an unverified identifier.
After successful verification, select **Remember this browser for 30 days** to skip the code on later sign-ins from that browser. New browsers still require a code, and signing out revokes the remembered browser. Do not select this option on a shared or public computer.

### Google sign-in

The sign-in page also supports Google OpenID Connect (OIDC). It uses the Google account's verified email as the user's identifier; email/phone OTP remains available as a fallback. Google sign-in is disabled until the OIDC settings are configured in `.streamlit/secrets.toml` (see `.streamlit/secrets.toml.example`).

Create a Google OAuth client ID for a web application. Add `http://localhost:8501/oauth2callback` as an authorized redirect URI in Google Cloud Console, and configure `[auth]` plus `[auth.google]` in the local secrets file. Generate `cookie_secret` with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

For deployment, set `redirect_uri` and Google's authorized redirect URI to the deployed app URL ending in `/oauth2callback`. Keep all client secrets out of source control. This Google app sign-in is separate from the OAuth connection under **Connected Accounts**, which grants Gmail mailbox access.

For email delivery over SMTP with STARTTLS, set:

- `EMAIL_SMTP_HOST` (required)
- `EMAIL_SMTP_PORT` (optional; defaults to `587`)
- `EMAIL_SMTP_USERNAME` and `EMAIL_SMTP_PASSWORD` (both required when SMTP authentication is used)
- `EMAIL_FROM_ADDRESS` (required unless `EMAIL_SMTP_USERNAME` is the sender address)

For SMS delivery with Twilio, set:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER` (a Twilio SMS-capable number)

Set environment variables in your Windows user environment or in the terminal session before launching Streamlit, then restart the app. Email codes are available only when SMTP is configured; phone codes are available only when Twilio is configured. This app does not store passwords: each code expires after five minutes, and verification allows five attempts. Drafts are scoped to the verified email address or phone number.

## How it works

1. The user chooses an action or lets the agent infer it from the instruction.
2. LangChain sends the task-specific prompt to the local Ollama model.
3. The app validates the JSON response before displaying it.
4. The user can edit the subject and body and save the draft.
5. SQLite keeps saved drafts available between application restarts.

## Limitations

Auto-detection uses simple intent keywords; choose an action manually if it is unclear. Model output quality depends on the local model. Review every draft before using it. The app does not connect to an email account or send messages.
For a public deployment, also configure HTTPS and deployment-level abuse controls; OTP rate limits in this local starter app are not a substitute for production identity infrastructure.

## Local implementation status

- SMTP sending, inbox reading, attachments, saved drafts, and per-user draft isolation are supported when the matching providers are configured.
- Local and cloud LLM providers are selectable from the sidebar.
- Drafts are encrypted and can be deleted from the history panel.
- Recipient formats and email send rate limits are validated locally.
- A full public email service still requires OAuth account connection flows, HTTPS hosting, production databases, audit logging, and deploy-level abuse controls.
