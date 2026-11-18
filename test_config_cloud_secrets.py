import unittest
from unittest.mock import patch

from config import get_settings


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
