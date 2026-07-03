"""Provider registry — resolve provider by name."""

import os

from workpilot.providers.base import LLMProvider
from workpilot.providers.stub import StubProvider

OPENAI_COMPATIBLE_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "glm": {
        "model": "glm-4",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "GLM_API_KEY",
    },
}


def get_provider(name: str, **kwargs) -> LLMProvider:
    """Get a provider instance by name.

    Args:
        name: Provider name (e.g. 'stub', 'openai', 'deepseek')
        **kwargs: Provider-specific configuration (api_key, model, base_url, etc.)

    Returns:
        An initialized LLMProvider instance.

    Raises:
        ValueError: If provider name is not registered.
    """
    if name == "stub":
        return StubProvider(**kwargs)

    if name in OPENAI_COMPATIBLE_DEFAULTS:
        from workpilot.providers.openai_provider import OpenAIProvider

        defaults = OPENAI_COMPATIBLE_DEFAULTS[name]
        api_key = kwargs.pop("api_key", None) or os.environ.get(defaults["env_key"], "")
        model = kwargs.pop("model", None) or defaults["model"]
        base_url = kwargs.pop("base_url", None) or defaults["base_url"]

        return OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            **kwargs,
        )

    available = ", ".join(sorted(["stub"] + list(OPENAI_COMPATIBLE_DEFAULTS.keys())))
    raise ValueError(f"Unknown provider '{name}'. Available: {available}")
