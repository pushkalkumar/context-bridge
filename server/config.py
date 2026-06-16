from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@lru_cache(maxsize=1)
def _probe_ollama() -> str | None:
    """One-shot probe of localhost:11434. Result is cached for the process lifetime."""
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=1.0)
        if r.status_code == 200:
            return "http://localhost:11434"
    except Exception:
        pass
    return None


class Settings(BaseSettings):
    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None
    ollama_host: str | None = None
    ollama_model: str = "qwen2.5-coder:7b"
    db_path: Path = Path.home() / ".context-bridge" / "checkpoints.db"
    server_port: int = 7723

    model_config = SettingsConfigDict(
        env_file=str(Path.home() / ".context-bridge" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolved_ollama_host(self) -> str | None:
        """OLLAMA_HOST if explicitly set; else probe localhost:11434 once per process."""
        return self.ollama_host or _probe_ollama()

    def embedding_api_key(self) -> str | None:
        """VOYAGE_API_KEY > ANTHROPIC_API_KEY for embedding calls."""
        return self.voyage_api_key or self.anthropic_api_key


settings = Settings()
