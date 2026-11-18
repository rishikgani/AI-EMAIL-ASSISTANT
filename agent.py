import json
import re
from typing import Any

from email_tools import EmailAction, EmailResult, detect_action
from prompts import build_messages


class OllamaUnavailableError(RuntimeError):
    """Raised when the local Ollama service cannot be reached."""


class ModelResponseError(ValueError):
    """Raised when the model response does not match the required result shape."""


def parse_model_response(content: str, action: EmailAction) -> EmailResult:
    cleaned = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    try:
        payload: Any = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise ModelResponseError("The model returned invalid JSON. Please try again.") from error
    if not isinstance(payload, dict):
        raise ModelResponseError("The model response must be a JSON object. Please try again.")

    if action == EmailAction.CATEGORIZE:
        category = payload.get("category")
        reason = payload.get("reason")
        if category not in {"Work", "Personal", "Urgent"} or not isinstance(reason, str) or not reason.strip():
            raise ModelResponseError("The model returned an invalid category. Please try again.")
        return EmailResult(action=action, category=category, reason=reason.strip())

    if action == EmailAction.SEND:
        to = payload.get("to", [])
        cc = payload.get("cc", [])
        bcc = payload.get("bcc", [])
        subject = payload.get("subject")
        body = payload.get("body")
        body_html = payload.get("body_html", "")
        attachments_data = payload.get("attachments", [])
        message_id = payload.get("message_id", "")
        in_reply_to = payload.get("in_reply_to", "")
        references = payload.get("references", [])

        if not isinstance(subject, str) or not subject.strip() or not isinstance(body, str) or not body.strip():
            raise ModelResponseError("The model did not return a valid subject and email body. Please try again.")
        if not isinstance(to, list) or not to or not all(isinstance(address, str) and address.strip() for address in to):
            raise ModelResponseError("The model did not return valid recipients. Please try again.")
        if not all(isinstance(addresses, list) and all(isinstance(address, str) and address.strip() for address in addresses)
                   for addresses in (cc, bcc)):
            raise ModelResponseError("The model did not return valid cc/bcc recipients. Please try again.")

        from email_tools import EmailAttachment
        attachments = []
        for att_data in attachments_data:
            if isinstance(att_data, dict):
                attachments.append(EmailAttachment(
                    filename=att_data.get("filename", ""),
                    content_type=att_data.get("content_type", ""),
                    data=att_data.get("data", ""),
                    size=att_data.get("size", 0),
                    inline_cid=att_data.get("inline_cid", ""),
                ))

        return EmailResult(
            action=action,
            subject=subject.strip(),
            body=body.strip(),
            body_html=body_html.strip() if body_html else "",
            to=to if isinstance(to, list) else [],
            cc=cc if isinstance(cc, list) else [],
            bcc=bcc if isinstance(bcc, list) else [],
            attachments=attachments,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references if isinstance(references, list) else [],
        )

    if action in (EmailAction.FETCH_INBOX, EmailAction.SEARCH_EMAILS):
        # These return structured data, not EmailResult
        # We'll handle them differently in the agent
        raise ModelResponseError("FETCH_INBOX and SEARCH_EMAILS should be handled by email_reader, not the model")

    if action == EmailAction.READ_THREAD:
        raise ModelResponseError("READ_THREAD should be handled by email_reader, not the model")

    if action == EmailAction.REWRITE_TONE:
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        body_html = payload.get("body_html", "")
        if not isinstance(subject, str) or not isinstance(body, str):
            raise ModelResponseError("Invalid rewrite response")
        return EmailResult(action=action, subject=subject.strip(), body=body.strip(), body_html=body_html.strip())

    if action == EmailAction.ADJUST_LENGTH:
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        body_html = payload.get("body_html", "")
        if not isinstance(subject, str) or not isinstance(body, str):
            raise ModelResponseError("Invalid length adjustment response")
        return EmailResult(action=action, subject=subject.strip(), body=body.strip(), body_html=body_html.strip())

    if action == EmailAction.CHECK_GRAMMAR:
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        body_html = payload.get("body_html", "")
        issues = payload.get("issues", [])
        if not isinstance(subject, str) or not isinstance(body, str):
            raise ModelResponseError("Invalid grammar check response")
        reason = json.dumps(issues)
        return EmailResult(action=action, subject=subject.strip(), body=body.strip(), body_html=body_html.strip(), reason=reason)

    if action == EmailAction.TRANSLATE:
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        body_html = payload.get("body_html", "")
        detected = payload.get("detected_language", "")
        target = payload.get("target_language", "")
        if not isinstance(subject, str) or not isinstance(body, str):
            raise ModelResponseError("Invalid translation response")
        reason = f"Translated from {detected} to {target}"
        return EmailResult(action=action, subject=subject.strip(), body=body.strip(), body_html=body_html.strip(), reason=reason)

    if action == EmailAction.SUMMARIZE:
        summary = payload.get("summary", "")
        key_points = payload.get("key_points", [])
        action_items = payload.get("action_items", [])
        if not isinstance(summary, str):
            raise ModelResponseError("Invalid summary response")
        reason = json.dumps({"key_points": key_points, "action_items": action_items})
        return EmailResult(action=action, subject="Summary", body=summary.strip(), reason=reason)

    if action == EmailAction.EXTRACT_ACTIONS:
        actions = payload.get("actions", [])
        if not isinstance(actions, list):
            raise ModelResponseError("Invalid action extraction response")
        body = "\n".join([f"- {a.get('description', '')} (Priority: {a.get('priority', 'medium')})" for a in actions])
        reason = json.dumps(actions)
        return EmailResult(action=action, subject="Extracted Actions", body=body.strip(), reason=reason)

    if action == EmailAction.SMART_REPLY:
        replies = payload.get("replies", [])
        if not isinstance(replies, list):
            raise ModelResponseError("Invalid smart reply response")
        body = "\n".join([f"- {r.get('text', '')} ({r.get('tone', 'neutral')})" for r in replies])
        reason = json.dumps(replies)
        return EmailResult(action=action, subject="Smart Replies", body=body.strip(), reason=reason)

    # Default: GENERATE or REPLY
    subject = payload.get("subject")
    body = payload.get("body")
    if not isinstance(subject, str) or not subject.strip() or not isinstance(body, str) or not body.strip():
        raise ModelResponseError("The model did not return a valid subject and email body. Please try again.")
    return EmailResult(action=action, subject=subject.strip(), body=body.strip())


class EmailAssistant:
    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        provider: str = "ollama",
        api_key: str | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = model
        self.api_key = api_key

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            self.model = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=0.2,
                format="json",
            )
        elif provider == "openai":
            from langchain_openai import ChatOpenAI
            if not api_key:
                raise ValueError("OpenAI API key required. Provide via parameter or OPENAI_API_KEY env var.")
            self.model = ChatOpenAI(
                model=model,
                api_key=api_key,
                temperature=0.2,
                model_kwargs={"response_format": {"type": "json_object"}},
            )
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            if not api_key:
                raise ValueError("Anthropic API key required. Provide via parameter or ANTHROPIC_API_KEY env var.")
            self.model = ChatAnthropic(
                model=model,
                api_key=api_key,
                temperature=0.2,
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def run(self, request: str, action: EmailAction | None = None) -> EmailResult:
        if not request.strip():
            raise ValueError("Enter an instruction or paste an email first.")
        selected_action = action or detect_action(request)
        if selected_action is None:
            raise ValueError(
                "I could not identify the task. Choose Generate Email, Suggest Reply, Categorize Email, or Send Email."
            )

        # Actions that don't use the model
        if selected_action in (EmailAction.FETCH_INBOX, EmailAction.READ_THREAD, EmailAction.SEARCH_EMAILS):
            from email_reader import EmailReader
            reader = EmailReader()
            if selected_action == EmailAction.FETCH_INBOX:
                emails = reader.fetch_inbox()
                return EmailResult(
                    action=selected_action,
                    subject="Inbox",
                    body=json.dumps([e.__dict__ for e in emails]),
                )
            elif selected_action == EmailAction.READ_THREAD:
                thread = reader.read_thread(request.strip())
                return EmailResult(
                    action=selected_action,
                    subject=thread.subject if thread else "Thread",
                    body=json.dumps(thread.__dict__) if thread else "{}",
                )
            elif selected_action == EmailAction.SEARCH_EMAILS:
                emails = reader.search_emails(request.strip())
                return EmailResult(
                    action=selected_action,
                    subject="Search Results",
                    body=json.dumps([e.__dict__ for e in emails]),
                )

        try:
            import httpx

            response = self.model.invoke(build_messages(request.strip(), selected_action))
        except httpx.HTTPError as error:
            if self.provider == "ollama":
                raise OllamaUnavailableError(
                    "Could not reach Ollama or load the selected model. Start Ollama, check the model name, "
                    "and try again."
                ) from error
            else:
                raise ModelResponseError(f"API request failed: {error}") from error
        except Exception as error:
            if self.provider != "ollama":
                raise ModelResponseError(f"API error: {error}") from error
            raise
        content = response.content
        if not isinstance(content, str):
            raise ModelResponseError("The model returned an unsupported response. Please try again.")
        return parse_model_response(content, selected_action)
