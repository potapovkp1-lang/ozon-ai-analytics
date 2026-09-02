from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ozon_client_id: str = ""
    ozon_api_key: str = ""
    ozon_performance_client_id: str = ""
    ozon_performance_client_secret: str = ""
    gpt_action_token: str = ""
    dashboard_username: str = ""
    dashboard_password: str = ""
    database_url: str = ""
    sync_enabled: bool = False
    timezone: str = "Europe/Moscow"
    default_sale_vat_rate: float = 22.0
    default_purchase_vat_rate: float = 22.0
    ozon_service_vat_rate: float = 22.0
    income_tax_rate: float = 25.0


settings = Settings()
