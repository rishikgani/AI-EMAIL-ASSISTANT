from email_tools import EmailAction


def build_messages(request: str, action: EmailAction) -> list[tuple[str, str]]:
    common = (
        "You are a professional email assistant. Use only facts supplied by the user. "
        "Do not invent names, dates, commitments, or other details. Use clear, polite language. "
        "If a detail is missing, use a clearly marked placeholder such as [Name]. "
        "Return only a valid JSON object, with no markdown or extra text."
    )
    if action == EmailAction.CATEGORIZE:
        task = (
            'Classify the message as exactly one of "Work", "Personal", or "Urgent". '
            'Return {"category":"Work|Personal|Urgent","reason":"brief explanation"}.'
        )
    elif action == EmailAction.REPLY:
        task = (
            'Draft a suitable reply to the incoming email. Return '
            '{"subject":"reply subject","body":"professional reply"}; do not claim the user '
            'will attend, agree, or take an action unless the request says so.'
        )
    elif action == EmailAction.SEND:
        task = (
            'Create a complete email ready to send. Return JSON with: '
            '"to": ["recipient@example.com"], "cc": [], "bcc": [], '
            '"subject": "email subject", "body": "plain text body", '
            '"body_html": "optional HTML body", '
            '"attachments": [{"filename": "file.pdf", "content_type": "application/pdf", "data": "base64", "size": 1234, "inline_cid": ""}], '
            '"in_reply_to": "optional message-id", "references": ["optional message-ids"]. '
            'Only include fields that have values.'
        )
    elif action == EmailAction.FETCH_INBOX:
        task = (
            'The user wants to see their inbox. Return a JSON array of email summaries with: '
            '"id", "thread_id", "from", "to", "subject", "snippet", "date", "labels", "is_read". '
            'Keep snippets under 200 characters.'
        )
    elif action == EmailAction.READ_THREAD:
        task = (
            'The user wants to read an email thread. Return a JSON object with: '
            '"thread_id", "subject", "participants", "messages" (array with id, from, to, subject, body_text, body_html, date, is_read), '
            '"last_message_at", "message_count".'
        )
    elif action == EmailAction.SEARCH_EMAILS:
        task = (
            'The user wants to search emails. Return a JSON array of matching emails with: '
            '"id", "thread_id", "from", "to", "subject", "snippet", "date", "labels", "is_read". '
            'Include search ranking score if available.'
        )
    elif action == EmailAction.REWRITE_TONE:
        task = (
            'Rewrite the email with the specified tone. Return '
            '{"subject":"rewritten subject","body":"rewritten body","body_html":"optional rewritten HTML"}. '
            'Tone options: formal, casual, friendly, professional, persuasive, empathetic.'
        )
    elif action == EmailAction.ADJUST_LENGTH:
        task = (
            'Adjust the email length. Return '
            '{"subject":"adjusted subject","body":"adjusted body","body_html":"optional adjusted HTML"}. '
            'Length options: concise, normal, detailed. Target word count may be specified.'
        )
    elif action == EmailAction.CHECK_GRAMMAR:
        task = (
            'Check grammar and spelling. Return '
            '{"subject":"corrected subject","body":"corrected body","body_html":"optional corrected HTML",'
            '"issues":[{"type":"grammar|spelling|style","original":"text","suggestion":"fix","position":123}]}'
        )
    elif action == EmailAction.TRANSLATE:
        task = (
            'Translate the email to the target language. Return '
            '{"subject":"translated subject","body":"translated body","body_html":"optional translated HTML",'
            '"detected_language":"source language","target_language":"target language"}.'
        )
    elif action == EmailAction.SUMMARIZE:
        task = (
            'Summarize the email or thread. Return '
            '{"summary":"concise summary","key_points":["point1","point2"],"action_items":["item1"]}.'
        )
    elif action == EmailAction.EXTRACT_ACTIONS:
        task = (
            'Extract action items from the email. Return '
            '{"actions":[{"description":"task description","deadline":"optional date","assignee":"optional person","priority":"high|medium|low"}]}'
        )
    elif action == EmailAction.SMART_REPLY:
        task = (
            'Suggest 3 short reply options. Return '
            '{"replies":[{"text":"short reply","tone":"formal|casual|friendly"}]}'
        )
    else:
        task = (
            'Write a new professional email based on the instruction. Return '
            '{"subject":"email subject","body":"professional email body"}.'
        )
    return [
        ("system", f"{common} {task}"),
        ("human", request),
    ]
