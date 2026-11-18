from dataclasses import asdict, dataclass
from enum import StrEnum


class EmailAction(StrEnum):
    GENERATE = "generate"
    REPLY = "reply"
    CATEGORIZE = "categorize"
    SEND = "send"
    FETCH_INBOX = "fetch_inbox"
    READ_THREAD = "read_thread"
    SEARCH_EMAILS = "search_emails"
    REWRITE_TONE = "rewrite_tone"
    ADJUST_LENGTH = "adjust_length"
    CHECK_GRAMMAR = "check_grammar"
    TRANSLATE = "translate"
    SUMMARIZE = "summarize"
    EXTRACT_ACTIONS = "extract_actions"
    SMART_REPLY = "smart_reply"


ACTION_LABELS = {
    EmailAction.GENERATE: "Generate Email",
    EmailAction.REPLY: "Suggest Reply",
    EmailAction.CATEGORIZE: "Categorize Email",
    EmailAction.SEND: "Send Email",
    EmailAction.FETCH_INBOX: "Fetch Inbox",
    EmailAction.READ_THREAD: "Read Thread",
    EmailAction.SEARCH_EMAILS: "Search Emails",
    EmailAction.REWRITE_TONE: "Rewrite Tone",
    EmailAction.ADJUST_LENGTH: "Adjust Length",
    EmailAction.CHECK_GRAMMAR: "Check Grammar",
    EmailAction.TRANSLATE: "Translate",
    EmailAction.SUMMARIZE: "Summarize",
    EmailAction.EXTRACT_ACTIONS: "Extract Actions",
    EmailAction.SMART_REPLY: "Smart Reply",
}


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content_type: str
    data: str  # base64 encoded
    size: int
    inline_cid: str = ""

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, str | int]) -> "EmailAttachment":
        return cls(**data)


@dataclass(frozen=True)
class EmailResult:
    action: EmailAction
    subject: str = ""
    body: str = ""
    body_html: str = ""
    category: str = ""
    reason: str = ""
    to: list[str] = None  # type: ignore
    cc: list[str] = None  # type: ignore
    bcc: list[str] = None  # type: ignore
    attachments: list[EmailAttachment] = None  # type: ignore
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.to is None:
            object.__setattr__(self, "to", [])
        if self.cc is None:
            object.__setattr__(self, "cc", [])
        if self.bcc is None:
            object.__setattr__(self, "bcc", [])
        if self.attachments is None:
            object.__setattr__(self, "attachments", [])
        if self.references is None:
            object.__setattr__(self, "references", [])

    def to_dict(self) -> dict[str, object]:
        result = {
            "action": self.action.value,
            "subject": self.subject,
            "body": self.body,
            "body_html": self.body_html,
            "category": self.category,
            "reason": self.reason,
            "to": self.to,
            "cc": self.cc,
            "bcc": self.bcc,
            "attachments": [a.to_dict() for a in self.attachments],
            "message_id": self.message_id,
            "in_reply_to": self.in_reply_to,
            "references": self.references,
        }
        return result


@dataclass(frozen=True)
class EmailMessage:
    id: str
    thread_id: str
    from_addr: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body_text: str
    body_html: str
    attachments: list[EmailAttachment]
    date: str
    labels: list[str]
    is_read: bool


@dataclass(frozen=True)
class EmailThread:
    thread_id: str
    subject: str
    participants: list[str]
    messages: list[EmailMessage]
    last_message_at: str
    message_count: int


def detect_action(request: str) -> EmailAction | None:
    text = request.casefold()
    categorize_terms = ("categorize", "categorise", "classify", "classification")
    reply_terms = ("reply", "respond", "response", "answer this email", "reply to")
    generate_terms = ("write", "draft", "compose", "create an email", "send an email")
    send_terms = ("send", "mail this", "send email", "email this", "dispatch")
    fetch_terms = ("fetch inbox", "get inbox", "show inbox", "list emails", "check email")
    thread_terms = ("read thread", "show thread", "view conversation", "open thread")
    search_terms = ("search", "find email", "look for")
    rewrite_terms = ("rewrite", "change tone", "make it")
    length_terms = ("shorten", "lengthen", "make shorter", "make longer", "adjust length")
    grammar_terms = ("grammar", "spell check", "proofread", "correct")
    translate_terms = ("translate", "convert to")
    summarize_terms = ("summarize", "summary", "tldr", "summarise")
    action_terms = ("extract actions", "action items", "tasks from")
    smart_reply_terms = ("smart reply", "quick reply", "suggest reply")

    matches = [
        action
        for action, terms in (
            (EmailAction.CATEGORIZE, categorize_terms),
            (EmailAction.REPLY, reply_terms),
            (EmailAction.GENERATE, generate_terms),
            (EmailAction.SEND, send_terms),
            (EmailAction.FETCH_INBOX, fetch_terms),
            (EmailAction.READ_THREAD, thread_terms),
            (EmailAction.SEARCH_EMAILS, search_terms),
            (EmailAction.REWRITE_TONE, rewrite_terms),
            (EmailAction.ADJUST_LENGTH, length_terms),
            (EmailAction.CHECK_GRAMMAR, grammar_terms),
            (EmailAction.TRANSLATE, translate_terms),
            (EmailAction.SUMMARIZE, summarize_terms),
            (EmailAction.EXTRACT_ACTIONS, action_terms),
            (EmailAction.SMART_REPLY, smart_reply_terms),
        )
        if any(term in text for term in terms)
    ]
    if not matches and any(term in text for term in ("need leave", "request leave")):
        return EmailAction.GENERATE
    if EmailAction.SMART_REPLY in matches:
        matches = [EmailAction.SMART_REPLY]
    if EmailAction.SEND in matches:
        matches = [EmailAction.SEND]
    if len(matches) == 1:
        return matches[0]
    if EmailAction.CATEGORIZE in matches:
        return None
    if EmailAction.REWRITE_TONE in matches:
        return None
    if EmailAction.ADJUST_LENGTH in matches:
        return None
    if EmailAction.CHECK_GRAMMAR in matches:
        return None
    if EmailAction.TRANSLATE in matches:
        return None
    if EmailAction.SUMMARIZE in matches:
        return None
    if EmailAction.EXTRACT_ACTIONS in matches:
        return None
    return matches[0] if len(matches) == 1 else None
