import unittest
from unittest.mock import patch

from config import get_settings, has_google_login_config


class CloudSecretsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_settings_load_provider_key_from_cloud_secrets(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("streamlit.secrets", new={"OPENAI_API_KEY": "cloud-test-key"}),
            patch("config.Path.exists", return_value=False),
        ):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.openai_api_key, "cloud-test-key")

    def test_google_login_requires_complete_oidc_settings(self) -> None:
        complete_auth = {
            "redirect_uri": "http://localhost:8501/oauth2callback",
            "cookie_secret": "test-cookie-secret",
            "google": {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
            },
        }
        with patch("config.read_streamlit_secrets", return_value={"auth": complete_auth}):
            self.assertTrue(has_google_login_config())
        with patch(
            "config.read_streamlit_secrets",
            return_value={"auth": {"google": {"client_id": "test-client-id"}}},
        ):
            self.assertFalse(has_google_login_config())
        complete_auth["google"]["client_secret"] = "your-google-oauth-client-secret"
        with patch("config.read_streamlit_secrets", return_value={"auth": complete_auth}):
            self.assertFalse(has_google_login_config())
