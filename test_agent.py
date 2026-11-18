import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import database
from agent import ModelResponseError, parse_model_response
from auth import (
    AuthDeliveryError,
    _load_settings,
    hash_verification_code,
    normalize_identifier,
    send_verification_code,
    verify_code,
)
from config import get_settings
from email_tools import EmailAction, detect_action


class ActionDetectionTests(unittest.TestCase):
    def test_detects_each_supported_action(self) -> None:
        self.assertEqual(detect_action("Please categorize this message"), EmailAction.CATEGORIZE)
        self.assertEqual(detect_action("Help me reply to this email"), EmailAction.REPLY)
        self.assertEqual(detect_action("Write a polite leave request"), EmailAction.GENERATE)
        self.assertEqual(detect_action("I am sick and need leave tomorrow"), EmailAction.GENERATE)

    def test_returns_none_for_unclear_or_conflicting_requests(self) -> None:
        self.assertIsNone(detect_action("Hello"))
        self.assertIsNone(detect_action("Write a reply to this message"))
        self.assertEqual(detect_action("Send an email to my manager"), EmailAction.SEND)
        self.assertEqual(detect_action("Give me smart reply options"), EmailAction.SMART_REPLY)


class ResponseValidationTests(unittest.TestCase):
    def test_parses_email_json_and_fenced_json(self) -> None:
        result = parse_model_response(
            '```json\n{"subject":"Hello","body":"Dear [Name], hello."}\n```',
            EmailAction.GENERATE,
        )
        self.assertEqual(result.subject, "Hello")
        self.assertEqual(result.body, "Dear [Name], hello.")

    def test_parses_supported_category(self) -> None:
        result = parse_model_response(
            '{"category":"Urgent","reason":"It needs attention today."}',
            EmailAction.CATEGORIZE,
        )
        self.assertEqual(result.category, "Urgent")

    def test_rejects_invalid_email_and_category_results(self) -> None:
        with self.assertRaises(ModelResponseError):
            parse_model_response('{"subject":"","body":"Body"}', EmailAction.GENERATE)
        with self.assertRaises(ModelResponseError):
            parse_model_response('{"category":"Other","reason":"Not supported"}', EmailAction.CATEGORIZE)
        with self.assertRaises(ModelResponseError):
            parse_model_response(
                '{"to":[123],"cc":[],"bcc":[],"subject":"Hello","body":"Body"}',
                EmailAction.SEND,
            )


class SignInTests(unittest.TestCase):
    def test_normalizes_email_and_validates_international_phone(self) -> None:
        self.assertEqual(normalize_identifier("Email", " User@Example.com "), "user@example.com")
        self.assertEqual(normalize_identifier("Phone", "+14155552671"), "+14155552671")

    def test_rejects_malformed_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            normalize_identifier("Email", "not-an-email")
        with self.assertRaises(ValueError):
            normalize_identifier("Phone", "4155552671")

    def test_verification_code_hash_is_salted_and_checked(self) -> None:
        digest = hash_verification_code("042731", "a-random-salt")
        self.assertTrue(verify_code("042731", "a-random-salt", digest))
        self.assertFalse(verify_code("042732", "a-random-salt", digest))
        self.assertNotEqual(digest, hash_verification_code("042731", "different-salt"))

    def test_delivery_reports_missing_provider_configuration(self) -> None:
        with patch("auth._load_settings", return_value={}):
            with self.assertRaisesRegex(AuthDeliveryError, "Email sign-in is not configured"):
                send_verification_code("Email", "user@example.com", "123456")
            with self.assertRaisesRegex(AuthDeliveryError, "Phone sign-in is not configured"):
                send_verification_code("Phone", "+14155552671", "123456")

    def test_email_and_sms_delivery_use_configured_providers(self) -> None:
        get_settings.cache_clear()
        email_settings = {
            "EMAIL_SMTP_HOST": "smtp.example.com",
            "EMAIL_SMTP_PORT": "587",
            "EMAIL_SMTP_USERNAME": "assistant@example.com",
            "EMAIL_SMTP_PASSWORD": "test-password",
            "EMAIL_FROM_ADDRESS": "assistant@example.com",
        }
        with patch.dict("os.environ", email_settings), patch("auth.smtplib.SMTP") as smtp:
            send_verification_code("Email", "user@example.com", "123456")
            smtp.return_value.__enter__.return_value.send_message.assert_called_once()

        get_settings.cache_clear()
        sms_settings = {
            "TWILIO_ACCOUNT_SID": "AC123",
            "TWILIO_AUTH_TOKEN": "test-token",
            "TWILIO_FROM_NUMBER": "+14155550000",
        }
        with patch.dict("os.environ", sms_settings), patch("auth.httpx.post") as post:
            send_verification_code("Phone", "+14155552671", "123456")
            post.assert_called_once()
            self.assertEqual(post.call_args.kwargs["data"]["To"], "+14155552671")

    def test_reads_provider_settings_from_local_secrets_file(self) -> None:
        get_settings.cache_clear()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.toml"
            path.write_text(
                'EMAIL_SMTP_HOST = "smtp.example.com"\n'
                'EMAIL_SMTP_PORT = "587"\n'
                'TWILIO_ACCOUNT_SID = "AC123"\n',
                encoding="utf-8",
            )
            with (
                patch("auth.SECRETS_PATH", path),
                patch("config.StreamlitSecretsSource.__call__", return_value={
                    "email_smtp_host": "smtp.example.com",
                    "email_smtp_port": "587",
                    "twilio_account_sid": "AC123",
                }),
                patch.dict("os.environ", {}, clear=True),
            ):
                settings = _load_settings()
            self.assertEqual(settings["EMAIL_SMTP_HOST"], "smtp.example.com")
            self.assertEqual(settings["EMAIL_SMTP_PORT"], "587")
            self.assertEqual(settings["TWILIO_ACCOUNT_SID"], "AC123")


class DraftDatabaseTests(unittest.TestCase):
    def tearDown(self):
        # Reset the global database instance to release file locks
        database._database = None
        database._test_db_path = None

    def test_adds_owner_column_to_existing_draft_database(self) -> None:
        directory = tempfile.mkdtemp()
        try:
            path = Path(directory) / "drafts.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE drafts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        subject TEXT NOT NULL,
                        body TEXT NOT NULL,
                        original_request TEXT NOT NULL,
                        action TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO drafts VALUES (1, 'Old', 'Draft', 'Request', 'generate', '', '', 'today')"
                )
            database.set_test_database_path(path)
            with patch("database.crypto_manager.is_encrypted", return_value=False):
                database.initialize_database()
            with closing(sqlite3.connect(path)) as connection:
                column_names = {row[1] for row in connection.execute("PRAGMA table_info(drafts)")}
            self.assertIn("owner_identifier", column_names)
            self.assertEqual(database.get_draft(1, "user@example.com"), None)
        finally:
            database._database = None
            database._test_db_path = None
            import shutil
            shutil.rmtree(directory, ignore_errors=True)

    def test_saved_draft_is_available_in_history_and_by_id(self) -> None:
        directory = tempfile.mkdtemp()
        try:
            path = Path(directory) / "drafts.sqlite3"
            database.set_test_database_path(path)
            draft_id = database.save_draft(
                owner_identifier="user@example.com",
                subject="A subject",
                body="An email body",
                original_request="Write an email",
                action="generate",
            )
            self.assertEqual(
                database.get_draft(draft_id, "user@example.com")["body"],
                "An email body",
            )
            self.assertIsNone(database.get_draft(draft_id, "other@example.com"))
            self.assertEqual(database.list_drafts("user@example.com")[0]["subject"], "A subject")
            self.assertEqual(database.list_drafts("other@example.com"), [])
            self.assertEqual(database.count_drafts("user@example.com"), 1)
            self.assertEqual(database.count_drafts("other@example.com"), 0)
        finally:
            database._database = None
            database._test_db_path = None
            import shutil
            shutil.rmtree(directory, ignore_errors=True)

    def test_login_challenge_throttles_and_counts_attempts_across_sessions(self) -> None:
        directory = tempfile.mkdtemp()
        try:
            path = Path(directory) / "drafts.sqlite3"
            database.set_test_database_path(path)
            wait = database.create_login_challenge(
                "user@example.com", "Email", "salt", "hash", 100.0, 400.0, 30
            )
            self.assertEqual(wait, 0)
            self.assertEqual(
                database.create_login_challenge(
                    "user@example.com", "Email", "new-salt", "new-hash", 110.0, 410.0, 30
                ),
                20,
            )
            self.assertEqual(database.record_login_attempt("user@example.com"), 1)
            self.assertEqual(database.get_login_challenge("user@example.com")["attempts"], 1)
            self.assertTrue(database.clear_login_challenge("user@example.com", "hash"))
            self.assertIsNone(database.get_login_challenge("user@example.com"))
        finally:
            database._database = None
            database._test_db_path = None
            import shutil
            shutil.rmtree(directory, ignore_errors=True)


class ConfigTests(unittest.TestCase):
    """Test config module functionality."""

    def test_settings_load_from_env(self) -> None:
        with patch.dict("os.environ", {
            "EMAIL_SMTP_HOST": "smtp.test.com",
            "EMAIL_SMTP_PORT": "465",
            "EMAIL_SMTP_TLS": "false",
            "OPENAI_API_KEY": "sk-test",
            "ANTHROPIC_API_KEY": "sk-ant-test",
        }):
            # Clear cache to reload
            get_settings.cache_clear()
            settings = get_settings()
            self.assertEqual(settings.email_smtp_host, "smtp.test.com")
            self.assertEqual(settings.email_smtp_port, 465)
            self.assertEqual(settings.email_smtp_tls, False)
            self.assertEqual(settings.openai_api_key, "sk-test")
            self.assertEqual(settings.anthropic_api_key, "sk-ant-test")

    def test_settings_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            get_settings.cache_clear()
            settings = get_settings()
            self.assertEqual(settings.email_smtp_port, 587)
            self.assertEqual(settings.email_imap_port, 993)
            self.assertTrue(settings.email_smtp_tls)
            self.assertEqual(settings.rate_limit_window, 60)
            self.assertEqual(settings.rate_limit_max, 30)

    def test_has_smtp_property(self) -> None:
        with patch.dict("os.environ", {
            "EMAIL_SMTP_HOST": "smtp.test.com",
            "EMAIL_FROM_ADDRESS": "test@test.com",
        }):
            get_settings.cache_clear()
            settings = get_settings()
            self.assertTrue(settings.has_smtp)

        with patch.dict("os.environ", {}, clear=True):
            get_settings.cache_clear()
            with patch("config.StreamlitSecretsSource.__call__", return_value={}):
                settings = get_settings()
            self.assertFalse(settings.has_smtp)

    def test_load_settings_dict(self) -> None:
        with patch.dict("os.environ", {
            "EMAIL_SMTP_HOST": "smtp.test.com",
            "EMAIL_SMTP_PORT": "587",
            "EMAIL_SMTP_USERNAME": "user",
            "EMAIL_SMTP_PASSWORD": "pass",
            "EMAIL_FROM_ADDRESS": "from@test.com",
            "EMAIL_SMTP_TLS": "true",
            "EMAIL_IMAP_HOST": "imap.test.com",
            "EMAIL_IMAP_PORT": "993",
            "EMAIL_IMAP_USERNAME": "imap_user",
            "EMAIL_IMAP_PASSWORD": "imap_pass",
            "GMAIL_CLIENT_ID": "gmail_id",
            "GMAIL_CLIENT_SECRET": "gmail_secret",
            "GMAIL_REFRESH_TOKEN": "gmail_refresh",
            "OUTLOOK_CLIENT_ID": "outlook_id",
            "OUTLOOK_CLIENT_SECRET": "outlook_secret",
            "OUTLOOK_REFRESH_TOKEN": "outlook_refresh",
            "TWILIO_ACCOUNT_SID": "twilio_sid",
            "TWILIO_AUTH_TOKEN": "twilio_token",
            "TWILIO_FROM_NUMBER": "+1234567890",
            "OPENAI_API_KEY": "openai_key",
            "ANTHROPIC_API_KEY": "anthropic_key",
            "EMAIL_ASSISTANT_DB": "/tmp/test.db",
            "ENCRYPTION_KEY": "enc_key",
        }):
            get_settings.cache_clear()
            from config import load_settings
            settings_dict = load_settings()
            self.assertEqual(settings_dict["EMAIL_SMTP_HOST"], "smtp.test.com")
            self.assertEqual(settings_dict["EMAIL_SMTP_PORT"], "587")
            self.assertEqual(settings_dict["GMAIL_CLIENT_ID"], "gmail_id")
            self.assertEqual(settings_dict["OPENAI_API_KEY"], "openai_key")


if __name__ == "__main__":
    unittest.main()
