from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://nexus:nexus@postgres:5432/nexus"

    # Redis
    redis_url: str = "redis://redis:6379"

    # GitHub App
    github_app_id: str = ""
    github_private_key_path: str = "./private-key.pem"
    github_webhook_secret: str = ""

    # Anthropic
    anthropic_api_key: str = ""


settings = Settings()
