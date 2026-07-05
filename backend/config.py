from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    DATABASE_URL: str = "postgresql+asyncpg://katha:katha@localhost:5432/katha"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"

    SARVAM_API_KEY: str
    ANTHROPIC_API_KEY: str


settings = Settings()
