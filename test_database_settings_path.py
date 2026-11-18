from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config import Settings
from database import Database


def test_database_uses_configured_path() -> None:
    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "drafts.sqlite3"
        settings = Settings(email_assistant_db=str(db_path))

        with patch("database.get_settings", return_value=settings):
            database = Database()

        assert database.db_path == db_path
