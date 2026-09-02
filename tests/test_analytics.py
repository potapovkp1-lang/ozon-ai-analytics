from app.config import Settings


def test_settings_have_safe_defaults():
    result = Settings()
    assert result.sync_enabled is False
    assert result.database_url == ""
