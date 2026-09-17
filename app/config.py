from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    proxy_api_key: str = ""
    openai_base_url: str = "https://api.proxyapi.ru/v1"
    openai_model: str = "gpt-4o-mini"
    rate_limit_per_minute: int = 10
    sqlite_path: str = "data/tickets.db"
    seed_demo: bool = True
    llm_timeout_seconds: float = 30.0

    def resolved_api_key(self) -> str:
        return (self.openai_api_key or self.proxy_api_key).strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
