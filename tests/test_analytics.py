from app.config import Settings


def test_settings_have_safe_defaults():
    result = Settings()
    assert result.sync_enabled is False
    assert result.database_url == ""
    assert result.default_sale_vat_rate == 22
    assert result.income_tax_rate == 25
