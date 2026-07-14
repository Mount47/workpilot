"""Anthropic Claude provider using the native messages API."""

import json
from time import monotonic
from typing import Any

from anthropic import Anthropic
from pydantic import BaseModel, ValidationError

from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    EvidenceType,
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
    StructuredGenerationResult,
)
from workpilot.providers.errors import ProviderCallError, classify_provider_exception
from workpilot.providers.openai_provider import (
    EVIDENCE_EXTRACTION_SYSTEM,
    EVIDENCE_EXTRACTION_USER,
    format_numbered_content,
)


class ClaudeProvider(LLMProvider):
    """Claude provider with the same observable boundary as other providers."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-5",
        base_url: str | None = None,
        max_retries: int = 1,
        timeout: float = 60.0,
        max_tokens: int = 4096,
        provider_name: str = "claude",
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be supplied by the Provider Registry")
        self.model = model
        self.provider_name = provider_name
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = Anthropic(**client_kwargs)

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        started = monotonic()
        try:
            response = self.client.messages.create(**kwargs)
        except Exception as exc:
            raise ProviderCallError(
                self.provider_name,
                classify_provider_exception(exc),
            ) from exc
        latency_ms = (monotonic() - started) * 1000
        parts = [block.text for block in response.content if block.type == "text"]
        usage = getattr(response, "usage", None)
        request_id = getattr(response, "id", None)
        stop_reason = getattr(response, "stop_reason", None)
        return GenerationResult(
            content="".join(parts),
            provider=self.provider_name,
            model=self.model,
            input_tokens=self._integer_attr(usage, "input_tokens"),
            output_tokens=self._integer_attr(usage, "output_tokens"),
            latency_ms=latency_ms,
            request_id=request_id if isinstance(request_id, str) else None,
            finish_reason=stop_reason if isinstance(stop_reason, str) else None,
        )

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        schema = response_model.model_json_schema()
        full_system = (
            f"Respond with JSON matching this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        if system_prompt:
            full_system = system_prompt + "\n\n" + full_system
        generations: list[GenerationResult] = []

        for attempt in range(1 + self.max_retries):
            generation = self.generate_text(
                prompt=prompt,
                system_prompt=full_system,
                temperature=temperature,
            )
            generations.append(generation)
            text = self._strip_markdown_fences(generation.content)
            try:
                value = response_model.model_validate(json.loads(text))
                return StructuredGenerationResult(
                    value=value,
                    generations=tuple(generations),
                )
            except (json.JSONDecodeError, ValidationError):
                if attempt == self.max_retries:
                    raise ProviderResponseError(
                        f"Failed to parse structured response after "
                        f"{1 + self.max_retries} attempts. Raw: {text[:200]}",
                        tuple(generations),
                    )

        raise RuntimeError("Unreachable")

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        if not content.strip():
            return EvidenceExtractionResult(candidates=[])
        user_prompt = EVIDENCE_EXTRACTION_USER.format(
            goal=goal,
            file_path=file_path,
            numbered_content=format_numbered_content(content),
        )
        generations: list[GenerationResult] = []
        for attempt in range(1 + self.max_retries):
            generation = self.generate_text(
                prompt=user_prompt,
                system_prompt=EVIDENCE_EXTRACTION_SYSTEM,
                temperature=0.0,
            )
            generations.append(generation)
            try:
                items = json.loads(self._strip_markdown_fences(generation.content))
                if not isinstance(items, list):
                    raise TypeError("evidence response is not a list")
                return EvidenceExtractionResult(
                    candidates=self._parse_candidates(items, file_path),
                    generations=tuple(generations),
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                if attempt == self.max_retries:
                    return EvidenceExtractionResult(
                        candidates=[],
                        generations=tuple(generations),
                        error_type="malformed_response",
                    )
        raise RuntimeError("Unreachable")

    @staticmethod
    def _integer_attr(obj: Any, name: str) -> int:
        value = getattr(obj, name, 0) if obj is not None else 0
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    @staticmethod
    def _parse_candidates(
        items: list[dict[str, Any]],
        file_path: str,
    ) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        valid_types = {item.value for item in EvidenceType}
        for item in items:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote", "")
            if not quote:
                continue
            evidence_type = item.get("evidence_type", "context")
            if evidence_type not in valid_types:
                evidence_type = EvidenceType.CONTEXT
            try:
                candidates.append(
                    EvidenceCandidate(
                        evidence_id="",
                        source_file=file_path,
                        quote=quote,
                        start_line=int(item.get("start_line", 1)),
                        end_line=int(item.get("end_line", 1)),
                        evidence_type=evidence_type,
                    )
                )
            except (ValueError, TypeError):
                continue
        return candidates

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n") if "\n" in text else len(text)
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
