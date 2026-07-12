"""Data-driven Provider descriptions and capability boundaries."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ProviderTransport(str, Enum):
    STUB = "stub"
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_NATIVE = "anthropic_native"


class ProviderCapabilities(BaseModel):
    """Capabilities exposed by the WorkPilot adapter, not the raw model."""

    text_generation: bool = True
    structured_output_mode: Literal["none", "validated_json", "native_schema"]
    reports_token_usage: bool = True
    native_tools: bool = False
    multimodal_input: bool = False


class ProviderDescriptor(BaseModel):
    """Configuration and observable capabilities for one Provider name."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    display_name: str = Field(min_length=1)
    transport: ProviderTransport
    default_model: str | None = None
    default_base_url: str | None = None
    capabilities: ProviderCapabilities


VALIDATED_TEXT_CAPABILITIES = ProviderCapabilities(
    structured_output_mode="validated_json",
)


PROVIDER_DESCRIPTORS: dict[str, ProviderDescriptor] = {
    "stub": ProviderDescriptor(
        name="stub",
        display_name="Deterministic Stub",
        transport=ProviderTransport.STUB,
        default_model="stub",
        capabilities=ProviderCapabilities(
            structured_output_mode="none",
            reports_token_usage=False,
        ),
    ),
    "openai": ProviderDescriptor(
        name="openai",
        display_name="OpenAI GPT",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_model="gpt-4o",
        default_base_url="https://api.openai.com/v1",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "claude": ProviderDescriptor(
        name="claude",
        display_name="Anthropic Claude",
        transport=ProviderTransport.ANTHROPIC_NATIVE,
        default_model="claude-sonnet-4-5",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "deepseek": ProviderDescriptor(
        name="deepseek",
        display_name="DeepSeek",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_model="deepseek-chat",
        default_base_url="https://api.deepseek.com/v1",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "qwen": ProviderDescriptor(
        name="qwen",
        display_name="Alibaba Qwen",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_model="qwen-plus",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "bailian": ProviderDescriptor(
        name="bailian",
        display_name="Alibaba Bailian",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_model="qwen-plus",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "glm": ProviderDescriptor(
        name="glm",
        display_name="Zhipu GLM",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_model="glm-4",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "gemini": ProviderDescriptor(
        name="gemini",
        display_name="Google Gemini",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_model="gemini-3.5-flash",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
    "custom": ProviderDescriptor(
        name="custom",
        display_name="Custom OpenAI-compatible Gateway",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        capabilities=VALIDATED_TEXT_CAPABILITIES,
    ),
}
