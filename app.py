import os
import platform
import re
import secrets
import shutil
import sys
import time

import httpx
import streamlit as st

from agent import EmailAssistant, ModelResponseError, OllamaUnavailableError
from attachment_handler import process_uploaded_file
from auth import (
    AuthDeliveryError,
    create_verification_code,
    hash_verification_code,
    mask_identifier,
    normalize_identifier,
    send_verification_code,
    verify_code,
)
from config import get_settings
from database import get_database, save_draft
from email_sender import send_email
from email_tools import ACTION_LABELS, EmailAction, EmailAttachment
from oauth_wizard import init_oauth_callback_handling, render_oauth_wizard
from session import get_session_manager

OTP_LIFETIME_SECONDS = 300
OTP_RESEND_SECONDS = 30
OTP_MAX_ATTEMPTS = 5


def is_smtp_configured() -> bool:
    """Check if SMTP settings are available for sending."""
    settings = get_settings()
    return settings.has_smtp


def parse_recipients(value: str) -> list[str]:
    return [address.strip() for address in value.split(",") if address.strip()]


def validate_recipients(addresses: list[str]) -> str | None:
    pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    invalid = [address for address in addresses if not pattern.fullmatch(address)]
    if invalid:
        return f"Invalid recipient address: {invalid[0]}"
    return None


st.set_page_config(
    page_title="AI Email Assistant",
    page_icon="assets/email_assistant_icon.svg",
    layout="wide",
)
st.logo(
    "assets/email_assistant_logo.svg",
    size="large",
    icon_image="assets/email_assistant_icon.svg",
)


def load_draft_into_editor(draft: object) -> None:
    if isinstance(draft, dict):
        draft_data = draft
    else:
        draft_data = {
            "action": getattr(draft, "reason", "") if getattr(draft, "reason", "") in ("generate", "reply", "send", "categorize") else "generate",
            "subject": draft.subject,
            "body": draft.body_text,
            "category": draft.category,
            "reason": draft.reason,
            "original_request": draft.original_request,
        }
    st.session_state["draft_result"] = {
        "action": draft_data["action"],
        "subject": draft_data["subject"],
        "body": draft_data["body"],
        "category": draft_data["category"],
        "reason": draft_data["reason"],
    }
    st.session_state["draft_request"] = draft_data["original_request"]
    st.session_state["subject_editor"] = draft_data["subject"]
    st.session_state["body_editor"] = draft_data["body"]


def get_or_create_session() -> str:
    """Get or create a session ID for the current user."""
    if "session_id" not in st.session_state:
        session_mgr = get_session_manager()
        session = session_mgr.create_session()
        st.session_state["session_id"] = session.session_id
    return st.session_state["session_id"]


def issue_sign_in_code(method: str, identifier: str) -> int:
    db = get_database()
    now = time.time()
    code = create_verification_code()
    salt = secrets.token_hex(16)
    code_hash = hash_verification_code(code, salt)
    wait_seconds = db.create_login_challenge(
        identifier,
        method,
        salt,
        code_hash,
        now,
        now + OTP_LIFETIME_SECONDS,
        OTP_RESEND_SECONDS,
    )
    st.session_state["auth_challenge"] = {"method": method, "identifier": identifier}
    if wait_seconds:
        return wait_seconds
    try:
        send_verification_code(method, identifier, code)
    except AuthDeliveryError:
        db.clear_login_challenge(identifier, code_hash)
        if st.session_state.get("auth_challenge", {}).get("identifier") == identifier:
            st.session_state.pop("auth_challenge", None)
        raise
    return 0


def resend_sign_in_code(method: str, identifier: str) -> None:
    try:
        wait_seconds = issue_sign_in_code(method, identifier)
        if wait_seconds:
            st.session_state["auth_notice"] = (
                "warning",
                f"A code was sent recently. Please wait {wait_seconds} seconds before requesting another.",
            )
        else:
            st.session_state["auth_notice"] = ("success", "A new verification code was sent.")
    except AuthDeliveryError as error:
        st.session_state["auth_notice"] = ("error", str(error))
    except ValueError as error:
        st.session_state["auth_notice"] = ("warning", str(error))


def render_sign_in() -> None:
    st.title("Sign in")
    st.caption("Use your email address or phone number. We'll send you a one-time verification code.")
    st.caption(
        "Email codes require SMTP settings; phone codes require Twilio settings. "
        "Add provider credentials to .streamlit/secrets.toml or your environment—never paste them here."
    )
    notice = st.session_state.pop("auth_notice", None)
    if notice:
        getattr(st, notice[0])(notice[1])
    challenge = st.session_state.get("auth_challenge")

    if challenge is None:
        with st.form("sign_in_form"):
            method = st.radio("Sign in with", ["Email", "Phone"], horizontal=True)
            identifier_input = st.text_input(
                "Email address" if method == "Email" else "Phone number",
                placeholder="you@example.com" if method == "Email" else "+14155552671",
                help="Phone numbers must include the country code, starting with +.",
            )
            send_clicked = st.form_submit_button("Send verification code", type="primary")
        if send_clicked:
            try:
                identifier = normalize_identifier(method, identifier_input)
                wait_seconds = issue_sign_in_code(method, identifier)
                if wait_seconds:
                    st.session_state["auth_notice"] = (
                        "warning",
                        f"A code was sent recently. Enter that code below; you can request another in {wait_seconds} seconds.",
                    )
                st.rerun()
            except AuthDeliveryError as error:
                st.error(str(error))
            except ValueError as error:
                st.warning(str(error))
    else:
        method = challenge["method"]
        identifier = challenge["identifier"]
        db = get_database()
        stored_challenge = db.get_login_challenge(identifier)
        if stored_challenge is None:
            st.session_state.pop("auth_challenge", None)
            st.warning("No active verification code was found. Request a new one.")
            st.rerun()
        st.info(
            f"Enter the six-digit code sent to {mask_identifier(method, identifier)}. "
            "The code expires in five minutes."
        )
        feedback = None
        if time.time() >= stored_challenge["expires_at"]:
            st.warning("That verification code has expired. Request a new one below.")
        elif stored_challenge["attempts"] >= OTP_MAX_ATTEMPTS:
            st.error("Too many incorrect attempts. Wait before requesting a new code.")
        else:
            with st.form("verify_code_form"):
                code_input = st.text_input(
                    "Verification code",
                    max_chars=6,
                    placeholder="123456",
                )
                verify_clicked = st.form_submit_button("Verify and sign in", type="primary")
            if verify_clicked:
                if (
                    len(code_input) == 6
                    and code_input.isascii()
                    and code_input.isdigit()
                    and verify_code(code_input, stored_challenge["salt"], stored_challenge["code_hash"])
                ):
                    if db.clear_login_challenge(identifier, stored_challenge["code_hash"]):
                        st.session_state["auth_verified"] = True
                        st.session_state["auth_identifier"] = identifier
                        st.session_state.pop("auth_challenge", None)
                        st.rerun()
                    feedback = "A newer code was requested. Enter the latest code."
                else:
                    attempts = db.record_login_attempt(identifier)
                    feedback = (
                        "Too many incorrect attempts. Wait before requesting a new code."
                        if attempts >= OTP_MAX_ATTEMPTS
                        else "That code is incorrect. Check it and try again."
                    )

        if feedback:
            st.error(feedback)
        remaining = max(
            0,
            int(
                OTP_RESEND_SECONDS
                - (time.time() - stored_challenge["created_at"])
            ),
        )
        st.button(
            f"Resend code ({remaining}s)" if remaining else "Resend code",
            disabled=remaining > 0,
            on_click=resend_sign_in_code,
            args=(method, identifier),
        )


if not st.session_state.get("auth_verified"):
    render_sign_in()
    st.stop()

# Initialize session for CSRF protection
session_id = get_or_create_session()
authenticated_identifier = st.session_state["auth_identifier"]


def render_email_assistant() -> None:
    st.title("AI Email Assistant Agent")
    st.caption("Create, refine, and organize professional email drafts with a local language model.")

    with st.sidebar:
        st.header("Model Settings")

        # Provider selection
        provider = st.radio(
            "LLM Provider",
            ["Ollama (Local)", "OpenAI", "Anthropic"],
            help="Choose local Ollama or cloud providers. Cloud providers require API keys in secrets.toml.",
        )

        if provider == "Ollama (Local)":
            model_name = st.text_input(
                "Ollama model",
                value="llama3.2",
                help="Use a model you have downloaded with Ollama.",
            )
            api_key = None
            st.caption("Your requests are sent to your local Ollama server. Emails are never sent by this app.")
        elif provider == "OpenAI":
            model_name = st.selectbox(
                "OpenAI model",
                ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
                index=0,
            )
            api_key = st.text_input(
                "OpenAI API Key",
                type="password",
                value=os.environ.get("OPENAI_API_KEY", ""),
                help="Stored in session only. Add to .streamlit/secrets.toml for persistence.",
            )
            st.caption("Uses OpenAI API. Requires valid API key with credits.")
        else:  # Anthropic
            model_name = st.selectbox(
                "Anthropic model",
                ["claude-3-5-haiku-latest", "claude-3-5-sonnet-latest", "claude-3-opus-latest"],
                index=1,
            )
            api_key = st.text_input(
                "Anthropic API Key",
                type="password",
                value=os.environ.get("ANTHROPIC_API_KEY", ""),
                help="Stored in session only. Add to .streamlit/secrets.toml for persistence.",
            )
            st.caption("Uses Anthropic API. Requires valid API key with credits.")

    st.subheader("What do you need?")
    with st.form("request_form"):
        action_choice = st.selectbox(
            "Action",
            ["Auto-detect", *[ACTION_LABELS[action] for action in EmailAction]],
            help="Choose Auto-detect to let the agent infer the task from your instruction.",
        )
        request_text = st.text_area(
            "Instruction or incoming email",
            placeholder="For example: Write a polite email asking my trainer for leave tomorrow because I am sick.",
            height=150,
        )
        generate_clicked = st.form_submit_button("Generate", type="primary")

    if generate_clicked:
        if not request_text.strip():
            st.warning("Enter an instruction or paste an email before generating.")
        else:
            selected_action = None
            if action_choice != "Auto-detect":
                selected_action = next(
                    action for action, label in ACTION_LABELS.items() if label == action_choice
                )
            try:
                provider_lower = provider.lower().replace(" (local)", "")
                with st.spinner(f"Working with {provider_lower}..."):
                    assistant = EmailAssistant(
                        model=model_name.strip() or "llama3.2",
                        provider=provider_lower,
                        api_key=api_key.strip() if api_key else None,
                    )
                    result = assistant.run(request_text.strip(), selected_action)
                st.session_state["draft_result"] = result.to_dict()
                st.session_state["draft_request"] = request_text.strip()
                st.session_state["subject_editor"] = result.subject
                st.session_state["body_editor"] = result.body
            except OllamaUnavailableError as error:
                st.error(str(error))
            except ModelResponseError as error:
                st.error(str(error))
            except ValueError as error:
                st.warning(str(error))

    result_data = st.session_state.get("draft_result")
    if result_data:
        st.divider()
        st.subheader("Review and edit your draft")
        if result_data.get("category"):
            st.info(f"Category: **{result_data['category']}** — {result_data.get('reason', '')}")

        # Recipient fields
        col1, col2 = st.columns([3, 1])
        with col1:
            to_input = st.text_input(
                "To *",
                value=result_data.get("to", [""])[0] if result_data.get("to") else "",
                placeholder="recipient@example.com",
                help="Required. Comma-separated for multiple recipients.",
            )
        with col2:
            cc_input = st.text_input(
                "Cc",
                value=", ".join(result_data.get("cc", [])) if result_data.get("cc") else "",
                placeholder="cc@example.com",
                help="Optional. Comma-separated.",
            )
        bcc_input = st.text_input(
            "Bcc",
            value=", ".join(result_data.get("bcc", [])) if result_data.get("bcc") else "",
            placeholder="bcc@example.com",
            help="Optional. Comma-separated.",
        )

        subject = st.text_input("Subject *", key="subject_editor")
        body = st.text_area("Email body *", key="body_editor", height=220)

        # Attachment uploader
        st.caption("Attachments")
        uploaded_files = st.file_uploader(
            "Add attachments (max 25MB each)",
            accept_multiple_files=True,
            type=["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "jpg", "jpeg", "png", "gif", "webp", "zip", "rar"],
            help="Supported: PDF, Office docs, images, text, archives. Max 25MB per file.",
        )

        # Process uploaded files
        uploaded_attachments = []
        if uploaded_files:
            for uploaded_file in uploaded_files:
                try:
                    attachment = process_uploaded_file(uploaded_file)
                    uploaded_attachments.append(attachment)
                    st.success(f"✓ {attachment.filename} ({attachment.size // 1024} KB)")
                except ValueError as e:
                    st.error(f"✗ {uploaded_file.name}: {e}")

        # Merge with any model-generated attachments
        all_attachments = []
        if result_data.get("attachments"):
            for att_data in result_data["attachments"]:
                all_attachments.append(EmailAttachment(
                    filename=att_data.get("filename", ""),
                    content_type=att_data.get("content_type", ""),
                    data=att_data.get("data", ""),
                    size=att_data.get("size", 0),
                    inline_cid=att_data.get("inline_cid", ""),
                ))
        all_attachments.extend(uploaded_attachments)

        # Action buttons
        btn_col1, btn_col2 = st.columns(2)
        smtp_ready = is_smtp_configured()

        with btn_col1:
            if st.button("Save draft", type="secondary", use_container_width=True):
                if not subject.strip() or not body.strip():
                    st.warning("A saved draft needs both a subject and an email body.")
                else:
                    draft_id = save_draft(
                        owner_identifier=authenticated_identifier,
                        subject=subject.strip(),
                        body=body.strip(),
                        original_request=st.session_state.get("draft_request", ""),
                        action=result_data["action"],
                        category=result_data.get("category", ""),
                        reason=result_data.get("reason", ""),
                        body_html=result_data.get("body_html", ""),
                        attachments=[attachment.to_dict() for attachment in all_attachments],
                    )
                    st.success(f"Draft saved (#{draft_id}).")

        with btn_col2:
            send_disabled = not smtp_ready
            send_help = None if smtp_ready else "Configure SMTP in .streamlit/secrets.toml to enable sending"
            if st.button(
                "Send email",
                type="primary",
                use_container_width=True,
                disabled=send_disabled,
                help=send_help,
            ):
                # Validate required fields
                if not to_input.strip():
                    st.warning("Recipient (To) is required to send.")
                elif not subject.strip() or not body.strip():
                    st.warning("Subject and body are required to send.")
                else:
                    try:
                        with st.spinner("Sending email..."):
                            to_list = parse_recipients(to_input)
                            cc_list = parse_recipients(cc_input)
                            bcc_list = parse_recipients(bcc_input)
                            recipient_error = validate_recipients(to_list + cc_list + bcc_list)
                            if recipient_error:
                                st.warning(recipient_error)
                                st.stop()

                            settings = get_settings()
                            allowed, remaining = get_database().check_rate_limit(
                                authenticated_identifier,
                                "send_email",
                                settings.rate_limit_max,
                                settings.rate_limit_window,
                            )
                            if not allowed:
                                st.warning("Email send limit reached. Please wait before sending more.")
                                st.stop()

                            # Handle attachments (model-generated + uploaded)
                            attachments = all_attachments

                            # Send via email_sender
                            send_result = send_email(
                                to=to_list,
                                subject=subject.strip(),
                                body_text=body.strip(),
                                body_html=result_data.get("body_html", ""),
                                cc=cc_list if cc_list else None,
                                bcc=bcc_list if bcc_list else None,
                                attachments=attachments if attachments else None,
                                in_reply_to=result_data.get("in_reply_to", "") or None,
                                references=result_data.get("references", []) or None,
                            )

                            if send_result.success:
                                st.success(f"Email sent! Message-ID: {send_result.message_id}")
                                # Optionally save to sent folder
                                save_draft(
                                    owner_identifier=authenticated_identifier,
                                    subject=subject.strip(),
                                    body=body.strip(),
                                    original_request=st.session_state.get("draft_request", ""),
                                    action="send",
                                    category=result_data.get("category", ""),
                                    reason=f"Sent via {send_result.provider} (Message-ID: {send_result.message_id})",
                                    body_html=result_data.get("body_html", ""),
                                    attachments=[attachment.to_dict() for attachment in attachments or []],
                                )
                            else:
                                st.error(f"Failed to send: {send_result.error}")
                    except Exception as e:
                        st.error(f"Send failed: {e}")

    st.divider()
    st.subheader("Saved drafts")
    db = get_database()
    drafts = db.list_drafts(owner_identifier=authenticated_identifier)
    if not drafts:
        st.caption("Your saved drafts will appear here.")
    else:
        draft_options = {
            f"#{draft.id} · {draft.subject} · {draft.created_at}": draft.id
            for draft in drafts
        }
        selected_label = st.selectbox("Open a saved draft", list(draft_options))
        selected_id = draft_options[selected_label]
        saved_draft = db.get_draft(selected_id, authenticated_identifier)
        if saved_draft:
            with st.expander("Draft details", expanded=True):
                st.markdown(f"**Subject:** {saved_draft.subject}")
                st.write(saved_draft.body_text)
                if saved_draft.category:
                    st.caption(f"{saved_draft.category} — {saved_draft.reason}")
                st.button(
                    "Load into editor",
                    key=f"load_{selected_id}",
                    on_click=load_draft_into_editor,
                    args=(saved_draft,),
                )
                if st.button("Delete draft", key=f"delete_{selected_id}") and db.delete_draft(selected_id, authenticated_identifier):
                        st.success("Draft deleted.")
                        st.rerun()


def render_host_dashboard(owner_identifier: str) -> None:
    st.title("Host dashboard")
    st.caption("Overview of this app host, local AI service, and draft storage.")

    try:
        response = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        response.raise_for_status()
        models = response.json().get("models", [])
        model_names = [model["name"] for model in models if isinstance(model, dict) and "name" in model]
        ollama_status = "Online"
    except httpx.HTTPError:
        model_names = []
        ollama_status = "Unavailable"
    except (ValueError, TypeError):
        model_names = []
        ollama_status = "Invalid response"

    db = get_database()
    try:
        draft_total = len(db.list_drafts(limit=1000, owner_identifier=owner_identifier))
        database_status = "Ready"
        database_bytes = db.db_path.stat().st_size if db.db_path.exists() else 0
    except (OSError, Exception) as error:
        draft_total = None
        database_status = f"Error: {error}"
        database_bytes = 0

    metrics = st.columns(2)
    metrics[0].metric("Streamlit app", "Online")
    metrics[1].metric("Ollama service", ollama_status)
    metrics = st.columns(2)
    metrics[0].metric("Installed models", len(model_names))
    metrics[1].metric("Saved drafts", draft_total if draft_total is not None else "Unavailable")

    st.subheader("Host and runtime")
    with st.container(border=True):
        st.write(f"**Host:** {platform.node()}")
        st.write(f"**Operating system:** {platform.system()} {platform.release()}")
        st.write(f"**Python:** {sys.version.split()[0]}")
        st.write(f"**Streamlit:** {st.__version__}")
        disk = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        st.write(
            f"**Project drive:** {disk.free / (1024**3):.1f} GB free "
            f"of {disk.total / (1024**3):.1f} GB"
        )

    left, right = st.columns(2)
    with left:
        st.subheader("Local AI service")
        with st.container(border=True):
            st.write(f"**Ollama:** {ollama_status}")
            st.write("**Endpoint:** `http://localhost:11434`")
            if model_names:
                st.write("**Available models:**")
                for model_name in model_names:
                    st.write(f"- {model_name}")
            elif ollama_status == "Online":
                st.caption("Ollama is running, but no models are installed.")
            else:
                st.caption("Start Ollama to enable local draft generation.")
    with right:
        st.subheader("Draft storage")
        with st.container(border=True):
            st.write(f"**Database:** `{db.db_path}`")
            st.write(f"**Status:** {database_status}")
            st.write(f"**File size:** {database_bytes / 1024:.1f} KB")
            if draft_total is not None:
                st.write(f"**Stored drafts:** {draft_total}")

    if st.button("Refresh host status", type="primary"):
        st.rerun()


with st.sidebar:
    st.caption(f"Signed in as {mask_identifier('Email' if '@' in authenticated_identifier else 'Phone', authenticated_identifier)}")
    if st.button("Sign out", width="stretch"):
        for key in (
            "auth_verified",
            "auth_identifier",
            "auth_challenge",
            "draft_result",
            "draft_request",
            "subject_editor",
            "body_editor",
            "session_id",
        ):
            st.session_state.pop(key, None)
        st.rerun()
    page = st.radio("Navigate", ["Email assistant", "Host dashboard"], label_visibility="collapsed")

if page == "Host dashboard":
    render_host_dashboard(authenticated_identifier)
else:
    render_email_assistant()
