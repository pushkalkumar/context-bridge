from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str | None = None
    # Set OLLAMA_HOST to use Ollama. Leave unset to auto-detect localhost:11434.
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
        """Return OLLAMA_HOST if set, or probe localhost:11434 as a fallback."""
        if self.ollama_host:
            return self.ollama_host
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=1.0)
            if r.status_code == 200:
                return "http://localhost:11434"
        except Exception:
            pass
        return None


settings = Settings()
