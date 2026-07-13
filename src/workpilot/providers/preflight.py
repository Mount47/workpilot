"""Offline Provider configuration checks that never expose API key values."""

from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from workpilot.config import Settings
from workpilot.providers.specs import ProviderTransport


class ProviderConfigurationError(ValueError):
    """A local Provider configuration is incomplete or unsafe."""

    error_type = "provider_configuration"
    retryable = False

    def __init__(self, provider: str, code: str, message: str) -> None:
        self.provider = provider
        self.code = code
        super().__init__(f"Provider '{provider}' configuration error [{code}]: {message}")


class ProviderPreflightIssue(BaseModel):
    """One sanitized local configuration issue."""

    code: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    message: str = Field(min_length=1)


class ProviderPreflightResult(BaseModel):
    """Secret-free readiness result; no API key value is stored."""

    provider: str
    transport: str
    model: str | None
    base_url: str | None
    api_key_env: str | None
    api_key_configured: bool
    structured_output_mode: str
    ready: bool
    issues: list[ProviderPreflightIssue] = Field(default_factory=list)

    def raise_for_errors(self) -> None:
        if self.ready:
            return
        issue = self.issues[0]
        raise ProviderConfigurationError(
            self.provider,
            issue.code,
            issue.message,
        )


def inspect_provider_configuration(
    provider: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key_configured: bool | None = None,
    required_capabilities: list[str] | None = None,
    settings: Settings | None = None,
) -> ProviderPreflightResult:
    """Check local readiness without constructing an SDK client or using network."""
    from workpilot.providers.registry import get_provider_descriptor

    descriptor = get_provider_descriptor(provider)
    resolved_settings = settings or Settings()
    resolved_model = model or resolved_settings.workpilot_model or descriptor.default_model
    resolved_base_url = (
        base_url
        or resolved_settings.workpilot_base_url
        or descriptor.default_base_url
    )
    key_env = Settings.api_key_env_for(provider)
    if descriptor.transport == ProviderTransport.STUB:
        key_is_configured = True
    elif api_key_configured is None:
        key_is_configured = bool(resolved_settings.api_key_for(provider).strip())
    else:
        key_is_configured = api_key_configured

    issues: list[ProviderPreflightIssue] = []
    if descriptor.transport != ProviderTransport.STUB and not key_is_configured:
        issues.append(
            ProviderPreflightIssue(
                code="missing_api_key",
                message=f"set {key_env} in the local environment or .env file",
            )
        )
    if not resolved_model:
        issues.append(
            ProviderPreflightIssue(
                code="missing_model",
                message="configure a model name",
            )
        )
    if descriptor.transport == ProviderTransport.OPENAI_COMPATIBLE:
        if not resolved_base_url:
            issues.append(
                ProviderPreflightIssue(
                    code="missing_base_url",
                    message="configure an API base URL",
                )
            )
        else:
            url_issue = _validate_base_url(resolved_base_url)
            if url_issue is not None:
                issues.append(url_issue)
    for capability in required_capabilities or []:
        if capability == "structured_output":
            supported = descriptor.capabilities.structured_output_mode != "none"
        else:
            supported = bool(getattr(descriptor.capabilities, capability, False))
        if not supported:
            issues.append(
                ProviderPreflightIssue(
                    code="missing_capability",
                    message=f"provider adapter lacks required capability {capability}",
                )
            )

    return ProviderPreflightResult(
        provider=provider,
        transport=descriptor.transport.value,
        model=resolved_model,
        base_url=_sanitize_base_url(resolved_base_url),
        api_key_env=key_env,
        api_key_configured=key_is_configured,
        structured_output_mode=descriptor.capabilities.structured_output_mode,
        ready=not issues,
        issues=issues,
    )


def _validate_base_url(base_url: str) -> ProviderPreflightIssue | None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ProviderPreflightIssue(
            code="unsafe_base_url",
            message="base URL must be an absolute HTTP(S) URL",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ProviderPreflightIssue(
            code="unsafe_base_url",
            message="base URL must not contain credentials, query, or fragment",
        )
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        return ProviderPreflightIssue(
            code="unsafe_base_url",
            message="non-local API base URL must use HTTPS",
        )
    return None


def _sanitize_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
