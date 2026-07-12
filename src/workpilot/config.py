"""Application configuration — loads from environment variables and a .env file.

Precedence (pydantic-settings default): explicit env vars > .env file > defaults.
Keeping every secret and run default in one typed object means the rest of the
code never reaches into os.environ directly — there is a single source of truth.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global settings for a WorkPilot run.

    API keys are grouped one-per-provider-family. `api_key_for` maps a provider
    name to the right key so the CLI/registry don't hardcode the mapping.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- API keys (one per provider family) ---
    openai_api_key: str = ""
    anthropic_api_key: str = ""       # Claude
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""       # Qwen / 通义千问 / 阿里云百炼
    glm_api_key: str = ""             # 智谱 ChatGLM
    gemini_api_key: str = ""          # Google Gemini
    custom_api_key: str = ""          # 企业自建 OpenAI-compatible Gateway

    # --- run defaults (overridable via CLI flags) ---
    workpilot_provider: str = Field(default="stub")
    workpilot_model: str = Field(default="")
    workpilot_base_url: str = Field(default="")
    max_steps: int = Field(default=30, ge=1)
    time_budget_seconds: int = Field(default=300, ge=1)
    token_budget: int = Field(default=100_000, ge=1)

    def api_key_for(self, provider: str) -> str:
        """Return the configured API key for a provider name (empty if unset)."""
        return {
            "openai": self.openai_api_key,
            "claude": self.anthropic_api_key,
            "deepseek": self.deepseek_api_key,
            "qwen": self.dashscope_api_key,
            "bailian": self.dashscope_api_key,
            "glm": self.glm_api_key,
            "gemini": self.gemini_api_key,
            "custom": self.custom_api_key,
        }.get(provider, "")
