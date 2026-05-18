from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENAI_API_KEY: str = ""
    SENTRY_DSN: str = ""

    DATABASE_URL: str = "postgresql+asyncpg://arya:arya@db:5432/arya"
    REDIS_URL: str = "redis://redis:6379/0"
    CORS_ORIGINS: str = "http://localhost:5173"

    OPENAI_REASONING_MODEL: str = "gpt-5.4-mini"
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    SHORTLIST_SIZE: int = 10
    LLM_CONCURRENCY: int = 5
    RESULT_CACHE_TTL_SECONDS: int = 86400

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
