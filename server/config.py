from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str | None = None
    ollama_host: str | None = None
    ollama_model: str = "qwen2.5-coder:7b"
    db_path: Path = Path.home() / ".context-bridge" / "checkpoints.db"
    server_port: int = 7723

    model_config = SettingsConfigDict(
        env_file=str(Path.home() / ".context-bridge" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
