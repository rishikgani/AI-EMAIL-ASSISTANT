"""
Email reading module supporting IMAP, Gmail API, and Outlook/Graph API.
"""

import base64
import email
import imaplib
import time
from dataclasses import dataclass
from email.header import decode_header
from email.utils import parseaddr

import httpx

from config import load_settings
from email_tools import EmailAttachment, EmailMessage, EmailThread


@dataclass
class EmailReaderConfig:
    """Configuration for email reader."""
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    use_gmail_api: bool = False
    use_outlook_api: bool = False


class EmailReader:
    """Unified email reader supporting multiple backends."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.config = self._load_config()

    def _load_config(self) -> EmailReaderConfig:
        return EmailReaderConfig(
            imap_host=self.settings.get("EMAIL_IMAP_HOST", ""),
            imap_port=int(self.settings.get("EMAIL_IMAP_PORT", "993")),
            imap_username=self.settings.get("EMAIL_IMAP_USERNAME", ""),
            imap_password=self.settings.get("EMAIL_IMAP_PASSWORD", ""),
            use_gmail_api=bool(
                self.settings.get("GMAIL_CLIENT_ID")
                and self.settings.get("GMAIL_CLIENT_SECRET")
                and self.settings.get("GMAIL_REFRESH_TOKEN")
            ),
            use_outlook_api=bool(
                self.settings.get("OUTLOOK_CLIENT_ID")
                and self.settings.get("OUTLOOK_CLIENT_SECRET")
                and self.settings.get("OUTLOOK_REFRESH_TOKEN")
            ),
        )

    def fetch_inbox(
        self,
        folder: str = "INBOX",
        limit: int = 50,
        offset: int = 0,
        unread_only: bool = False,
    ) -> list[EmailMessage]:
        """Fetch emails from inbox."""
        if self.config.use_gmail_api:
            return self._fetch_gmail_api(folder, limit, offset, unread_only)
        elif self.config.use_outlook_api:
            return self._fetch_outlook_api(folder, limit, offset, unread_only)
        elif self.config.imap_host:
            return self._fetch_imap(folder, limit, offset, unread_only)
        else:
            raise RuntimeError("No email reading backend configured. Set IMAP, Gmail API, or Outlook API credentials.")

    def read_thread(self, thread_id: str) -> EmailThread | None:
        """Read a full email thread/conversation."""
        if self.config.use_gmail_api:
            return self._read_thread_gmail_api(thread_id)
        elif self.config.use_outlook_api:
            return self._read_thread_outlook_api(thread_id)
        elif self.config.imap_host:
            return self._read_thread_imap(thread_id)
        else:
            raise RuntimeError("No email reading backend configured.")

    def search_emails(
        self,
        query: str,
        folder: str = "INBOX",
        limit: int = 50,
    ) -> list[EmailMessage]:
        """Search emails."""
        if self.config.use_gmail_api:
            return self._search_gmail_api(query, folder, limit)
        elif self.config.use_outlook_api:
            return self._search_outlook_api(query, folder, limit)
        elif self.config.imap_host:
            return self._search_imap(query, folder, limit)
        else:
            raise RuntimeError("No email reading backend configured.")

    # IMAP Implementation
    def _fetch_imap(
        self,
        folder: str,
        limit: int,
        offset: int,
        unread_only: bool,
    ) -> list[EmailMessage]:
        """Fetch emails via IMAP."""
        conn = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        try:
            conn.login(self.config.imap_username, self.config.imap_password)
            conn.select(folder)

            search_criteria = "UNSEEN" if unread_only else "ALL"
            status, messages = conn.search(None, search_criteria)
            if status != "OK":
                return []

            msg_nums = messages[0].split()
            msg_nums = msg_nums[-(limit + offset):-offset] if offset else msg_nums[-limit:]
            msg_nums.reverse()  # Newest first

            emails = []
            for num in msg_nums:
                status, data = conn.fetch(num, "(RFC822)")
                if status == "OK":
                    raw_email = data[0][1]
                    parsed = self._parse_email(raw_email)
                    if parsed:
                        emails.append(parsed)
            return emails
        finally:
            try:
                conn.close()
                conn.logout()
            except Exception:
                pass

    def _read_thread_imap(self, thread_id: str) -> EmailThread | None:
        """Read thread via IMAP using References/In-Reply-To headers."""
        # IMAP doesn't have native threading, so we search by References
        conn = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        try:
            conn.login(self.config.imap_username, self.config.imap_password)
            conn.select("INBOX")

            # Search for messages with this thread_id in References
            status, data = conn.search(None, f'HEADER References "{thread_id}"')
            if status != "OK":
                return None

            msg_nums = data[0].split()
            if not msg_nums:
                return None

            messages = []
            for num in msg_nums:
                status, msg_data = conn.fetch(num, "(RFC822)")
                if status == "OK":
                    parsed = self._parse_email(msg_data[0][1])
                    if parsed:
                        messages.append(parsed)

            # Also check In-Reply-To
            status, data = conn.search(None, f'HEADER In-Reply-To "{thread_id}"')
            if status == "OK":
                for num in data[0].split():
                    status, msg_data = conn.fetch(num, "(RFC822)")
                    if status == "OK":
                        parsed = self._parse_email(msg_data[0][1])
                        if parsed and parsed.id not in [m.id for m in messages]:
                            messages.append(parsed)

            if not messages:
                return None

            messages.sort(key=lambda m: m.date)
            all_addrs = {messages[0].from_addr}
            for msg in messages:
                all_addrs.update(msg.to)
            return EmailThread(
                thread_id=thread_id,
                subject=messages[0].subject,
                participants=list(all_addrs),
                messages=messages,
                last_message_at=messages[-1].date,
                message_count=len(messages),
            )
        finally:
            try:
                conn.close()
                conn.logout()
            except Exception:
                pass

    def _search_imap(self, query: str, folder: str, limit: int) -> list[EmailMessage]:
        """Search emails via IMAP."""
        conn = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        try:
            conn.login(self.config.imap_username, self.config.imap_password)
            conn.select(folder)

            # IMAP search - search in subject, from, body
            search_query = f'(OR SUBJECT "{query}" OR FROM "{query}" OR BODY "{query}")'
            status, data = conn.search(None, search_query)
            if status != "OK":
                return []

            msg_nums = data[0].split()[-limit:]
            msg_nums.reverse()

            emails = []
            for num in msg_nums:
                status, msg_data = conn.fetch(num, "(RFC822)")
                if status == "OK":
                    parsed = self._parse_email(msg_data[0][1])
                    if parsed:
                        emails.append(parsed)
            return emails
        finally:
            try:
                conn.close()
                conn.logout()
            except Exception:
                pass

    def _parse_email(self, raw_email: bytes) -> EmailMessage | None:
        """Parse raw email bytes into EmailMessage."""
        try:
            msg = email.message_from_bytes(raw_email)

            # Extract headers
            subject = self._decode_header(msg.get("Subject", ""))
            from_raw = msg.get("From", "")
            from_addr = parseaddr(from_raw)[1]
            to_raw = msg.get("To", "")
            to = [parseaddr(addr)[1] for addr in to_raw.split(",")] if to_raw else []
            cc_raw = msg.get("Cc", "")
            cc = [parseaddr(addr)[1] for addr in cc_raw.split(",")] if cc_raw else []
            bcc_raw = msg.get("Bcc", "")
            bcc = [parseaddr(addr)[1] for addr in bcc_raw.split(",")] if bcc_raw else []
            date = msg.get("Date", "")
            message_id = msg.get("Message-ID", "")
            in_reply_to = msg.get("In-Reply-To", "")
            references = msg.get("References", "").split() if msg.get("References") else []

            # Extract body
            body_text = ""
            body_html = ""
            attachments = []

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    payload = part.get_payload(decode=True)

                    if payload is None:
                        continue

                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        body_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    elif content_type == "text/html" and "attachment" not in content_disposition:
                        body_html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    elif "attachment" in content_disposition or part.get_filename():
                        filename = part.get_filename()
                        if filename:
                            filename = self._decode_header(filename)
                        else:
                            filename = f"attachment_{len(attachments)}"
                        attachments.append(EmailAttachment(
                            filename=filename,
                            content_type=content_type,
                            data=base64.b64encode(payload).decode(),
                            size=len(payload),
                        ))
            else:
                content_type = msg.get_content_type()
                payload = msg.get_payload(decode=True)
                if payload:
                    if content_type == "text/html":
                        body_html = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                    else:
                        body_text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

            # Generate thread_id from References or In-Reply-To
            thread_id = references[0] if references else (in_reply_to or message_id)

            return EmailMessage(
                id=message_id or str(int(time.time() * 1000)),
                thread_id=thread_id,
                from_addr=from_addr,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
                date=date,
                labels=[],
                is_read=True,  # IMAP doesn't easily track read status without \Seen flag
            )
        except Exception:
            return None

    def _decode_header(self, header: str) -> str:
        """Decode email header."""
        if not header:
            return ""
        decoded = decode_header(header)
        parts = []
        for part, encoding in decoded:
            if isinstance(part, bytes):
                parts.append(part.decode(encoding or "utf-8", errors="replace"))
            else:
                parts.append(part)
        return " ".join(parts)

    # Gmail API Implementation
    def _get_gmail_access_token(self) -> str | None:
        client_id = self.settings.get("GMAIL_CLIENT_ID")
        client_secret = self.settings.get("GMAIL_CLIENT_SECRET")
        refresh_token = self.settings.get("GMAIL_REFRESH_TOKEN")

        if not all([client_id, client_secret, refresh_token]):
            return None

        try:
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json().get("access_token")
        except httpx.HTTPError:
            return None

    def _fetch_gmail_api(
        self,
        folder: str,
        limit: int,
        offset: int,
        unread_only: bool,
    ) -> list[EmailMessage]:
        access_token = self._get_gmail_access_token()
        if not access_token:
            raise RuntimeError("Failed to obtain Gmail access token")

        # Map folder to Gmail label
        label_map = {
            "INBOX": "INBOX",
            "SENT": "SENT",
            "DRAFT": "DRAFT",
            "SPAM": "SPAM",
            "TRASH": "TRASH",
        }
        label = label_map.get(folder.upper(), "INBOX")

        query = f"label:{label}"
        if unread_only:
            query += " is:unread"

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "q": query,
            "maxResults": limit,
            "pageToken": None,
        }

        # Handle pagination with offset
        if offset > 0:
            # Fetch and discard offset messages
            pass

        response = httpx.get(
            "https://www.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        emails = []
        for msg_ref in data.get("messages", []):
            msg_detail = self._get_gmail_message(msg_ref["id"], access_token)
            if msg_detail:
                emails.append(msg_detail)
        return emails

    def _get_gmail_message(self, msg_id: str, access_token: str) -> EmailMessage | None:
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"format": "full"}
        response = httpx.get(
            f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        return self._parse_gmail_message(data)

    def _parse_gmail_message(self, data: dict) -> EmailMessage | None:
        try:
            payload = data.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

            subject = headers.get("Subject", "")
            from_raw = headers.get("From", "")
            from_addr = parseaddr(from_raw)[1]
            to_raw = headers.get("To", "")
            to = [parseaddr(addr.strip())[1] for addr in to_raw.split(",")] if to_raw else []
            cc_raw = headers.get("Cc", "")
            cc = [parseaddr(addr.strip())[1] for addr in cc_raw.split(",")] if cc_raw else []
            date = headers.get("Date", "")
            message_id = headers.get("Message-ID", "")
            headers.get("In-Reply-To", "")
            references = headers.get("References", "").split() if headers.get("References") else []
            thread_id = data.get("threadId", references[0] if references else message_id)

            # Extract body
            body_text = ""
            body_html = ""
            attachments = []

            def extract_parts(part: dict):
                nonlocal body_text, body_html, attachments
                mime_type = part.get("mimeType", "")
                body = part.get("body", {})
                data = body.get("data", "")
                attachment_id = body.get("attachmentId", "")

                if mime_type == "text/plain":
                    if data:
                        body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                elif mime_type == "text/html":
                    if data:
                        body_html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                elif attachment_id or part.get("filename"):
                    # Fetch attachment
                    att_id = attachment_id or part.get("body", {}).get("attachmentId")
                    if att_id:
                        att_data = self._get_gmail_attachment(message_id, att_id, headers.get("Authorization", ""))
                        if att_data:
                            filename = part.get("filename", f"attachment_{len(attachments)}")
                            attachments.append(EmailAttachment(
                                filename=filename,
                                content_type=mime_type,
                                data=att_data,
                                size=len(base64.b64decode(att_data)),
                            ))

                for subpart in part.get("parts", []):
                    extract_parts(subpart)

            extract_parts(payload)

            # Handle labels
            labels = data.get("labelIds", [])
            is_read = "UNREAD" not in labels

            return EmailMessage(
                id=message_id or data.get("id", ""),
                thread_id=thread_id,
                from_addr=from_addr,
                to=to,
                cc=cc,
                bcc=[],
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
                date=date,
                labels=labels,
                is_read=is_read,
            )
        except Exception:
            return None

    def _get_gmail_attachment(self, message_id: str, attachment_id: str, auth_header: str) -> str | None:
        try:
            headers = {"Authorization": auth_header}
            response = httpx.get(
                f"https://www.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}",
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("data", "")
        except httpx.HTTPError:
            return None

    def _read_thread_gmail_api(self, thread_id: str) -> EmailThread | None:
        access_token = self._get_gmail_access_token()
        if not access_token:
            raise RuntimeError("Failed to obtain Gmail access token")

        headers = {"Authorization": f"Bearer {access_token}"}
        response = httpx.get(
            f"https://www.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        messages = []
        for msg in data.get("messages", []):
            parsed = self._parse_gmail_message(msg)
            if parsed:
                messages.append(parsed)

        if not messages:
            return None

        messages.sort(key=lambda m: m.date)
        all_addrs = {messages[0].from_addr}
        for msg in messages:
            all_addrs.update(msg.to)
        participants = list(all_addrs)

        return EmailThread(
            thread_id=thread_id,
            subject=messages[0].subject if messages else "",
            participants=participants,
            messages=messages,
            last_message_at=messages[-1].date if messages else "",
            message_count=len(messages),
        )

    def _search_gmail_api(self, query: str, folder: str, limit: int) -> list[EmailMessage]:
        access_token = self._get_gmail_access_token()
        if not access_token:
            raise RuntimeError("Failed to obtain Gmail access token")

        label_map = {
            "INBOX": "INBOX",
            "SENT": "SENT",
            "DRAFT": "DRAFT",
        }
        label = label_map.get(folder.upper(), "INBOX")
        full_query = f"label:{label} {query}"

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"q": full_query, "maxResults": limit}
        response = httpx.get(
            "https://www.googleapis.com/gmail/v1/users/me/messages",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        emails = []
        for msg_ref in data.get("messages", []):
            msg_detail = self._get_gmail_message(msg_ref["id"], access_token)
            if msg_detail:
                emails.append(msg_detail)
        return emails

    # Outlook/Graph API Implementation
    def _get_outlook_access_token(self) -> str | None:
        client_id = self.settings.get("OUTLOOK_CLIENT_ID")
        client_secret = self.settings.get("OUTLOOK_CLIENT_SECRET")
        refresh_token = self.settings.get("OUTLOOK_REFRESH_TOKEN")

        if not all([client_id, client_secret, refresh_token]):
            return None

        try:
            response = httpx.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json().get("access_token")
        except httpx.HTTPError:
            return None

    def _fetch_outlook_api(self, folder: str, limit: int, offset: int, unread_only: bool) -> list[EmailMessage]:
        access_token = self._get_outlook_access_token()
        if not access_token:
            raise RuntimeError("Failed to obtain Outlook access token")

        folder_id = self._get_outlook_folder_id(folder, access_token)
        if not folder_id:
            raise RuntimeError(f"Folder not found: {folder}")

        headers = {"Authorization": f"Bearer {access_token}"}
        filter_query = ""
        if unread_only:
            filter_query = "isRead eq false"

        params = {
            "$top": limit,
            "$skip": offset,
            "$orderby": "receivedDateTime desc",
        }
        if filter_query:
            params["$filter"] = filter_query

        response = httpx.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/messages",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        emails = []
        for msg in data.get("value", []):
            parsed = self._parse_outlook_message(msg)
            if parsed:
                emails.append(parsed)
        return emails

    def _get_outlook_folder_id(self, folder_name: str, access_token: str) -> str | None:
        folder_map = {
            "INBOX": "inbox",
            "SENT": "sentitems",
            "DRAFT": "drafts",
            "SPAM": "junkemail",
            "TRASH": "deleteditems",
        }
        return folder_map.get(folder_name.upper(), "inbox")

    def _parse_outlook_message(self, data: dict) -> EmailMessage | None:
        try:
            message_id = data.get("internetMessageId", data.get("id", ""))
            thread_id = data.get("conversationId", message_id)

            from_data = data.get("from", {}).get("emailAddress", {})
            from_addr = from_data.get("address", "")

            to = [r.get("emailAddress", {}).get("address", "") for r in data.get("toRecipients", [])]
            cc = [r.get("emailAddress", {}).get("address", "") for r in data.get("ccRecipients", [])]

            subject = data.get("subject", "")
            date = data.get("receivedDateTime", "")
            is_read = data.get("isRead", True)

            body = data.get("body", {})
            body_content = body.get("content", "")
            body_type = body.get("contentType", "text")

            body_text = body_content if body_type == "text" else ""
            body_html = body_content if body_type == "html" else ""

            # Attachments would need separate API call
            attachments = []

            return EmailMessage(
                id=message_id,
                thread_id=thread_id,
                from_addr=from_addr,
                to=to,
                cc=cc,
                bcc=[],
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                attachments=attachments,
                date=date,
                labels=[],
                is_read=is_read,
            )
        except Exception:
            return None

    def _read_thread_outlook_api(self, thread_id: str) -> EmailThread | None:
        access_token = self._get_outlook_access_token()
        if not access_token:
            raise RuntimeError("Failed to obtain Outlook access token")

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "$filter": f"conversationId eq '{thread_id}'",
            "$orderby": "receivedDateTime asc",
            "$top": 100,
        }
        response = httpx.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        messages = []
        for msg in data.get("value", []):
            parsed = self._parse_outlook_message(msg)
            if parsed:
                messages.append(parsed)

        if not messages:
            return None

        all_addrs = {messages[0].from_addr}
        for msg in messages:
            all_addrs.update(msg.to)
        participants = list(all_addrs)

        return EmailThread(
            thread_id=thread_id,
            subject=messages[0].subject if messages else "",
            participants=participants,
            messages=messages,
            last_message_at=messages[-1].date if messages else "",
            message_count=len(messages),
        )

    def _search_outlook_api(self, query: str, folder: str, limit: int) -> list[EmailMessage]:
        access_token = self._get_outlook_access_token()
        if not access_token:
            raise RuntimeError("Failed to obtain Outlook access token")

        folder_id = self._get_outlook_folder_id(folder, access_token)
        if not folder_id:
            raise RuntimeError(f"Folder not found: {folder}")

        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "$search": f"\"{query}\"",
            "$top": limit,
            "$orderby": "receivedDateTime desc",
        }
        response = httpx.get(
            f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/messages",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        emails = []
        for msg in data.get("value", []):
            parsed = self._parse_outlook_message(msg)
            if parsed:
                emails.append(parsed)
        return emails
