from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ozon_client_id: str = ""
    ozon_api_key: str = ""
    ozon_performance_client_id: str = ""
    ozon_performance_client_secret: str = ""
    gpt_action_token: str = ""
    database_url: str = ""
    sync_enabled: bool = False
    timezone: str = "Europe/Moscow"


settings = Settings()
