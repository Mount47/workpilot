"""OpenAI-compatible provider — works with OpenAI, DeepSeek, Qwen and GLM."""

import json
from time import monotonic
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    EvidenceType,
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
    StructuredGenerationResult,
    structured_validation_feedback,
)
from workpilot.providers.errors import ProviderCallError, classify_provider_exception

EVIDENCE_EXTRACTION_SYSTEM = """You are an evidence extraction engine for project reports.
Given a source file from a project workspace, identify factual statements that serve as evidence for a weekly status report.

Rules:
- Extract ONLY statements that are explicitly written in the file. Never infer or paraphrase.
- Each quote must be an EXACT substring of the source file content.
- Classify each piece of evidence by type: progress, decision, risk, blocker, action_item, context, requirement_change.
- Include the exact line numbers (1-indexed) where the quote appears.
- Source lines are prefixed with `N |` for location only. Never include this prefix in quote.
- Preserve Markdown list markers such as `- ` and numeric markers such as `1. ` in quote.
- If the file has no useful evidence for the goal, return an empty list.

Respond with a JSON array of objects:
[
  {
    "quote": "exact text from the file",
    "start_line": 1,
    "end_line": 1,
    "evidence_type": "progress"
  }
]

Only output valid JSON. No markdown fences, no explanation."""

EVIDENCE_EXTRACTION_USER = """Goal: {goal}

File: {file_path}

Numbered source content (`N |` is not part of the source text):
{numbered_content}

Extract evidence relevant to the goal. Return a JSON array."""


def format_numbered_content(content: str) -> str:
    """Add stable visual line numbers without changing the source itself."""
    return "\n".join(
        f"{line_number} | {line}"
        for line_number, line in enumerate(content.splitlines(), start=1)
    )


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider with observable call results."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str | None = None,
        max_retries: int = 1,
        timeout: float = 60.0,
        provider_name: str = "openai",
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be supplied by the Provider Registry")

        self.model = model
        self.provider_name = provider_name
        self.max_retries = max_retries
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._create_completion(messages, temperature)

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
        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": prompt},
        ]
        generations: list[GenerationResult] = []

        for attempt in range(1 + self.max_retries):
            generation = self._create_completion(messages, temperature)
            generations.append(generation)
            text = self._strip_markdown_fences(generation.content)
            try:
                value = response_model.model_validate(json.loads(text))
                return StructuredGenerationResult(
                    value=value,
                    generations=tuple(generations),
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                if attempt == self.max_retries:
                    raise ProviderResponseError(
                        "Structured response validation failed after "
                        f"{1 + self.max_retries} attempts; "
                        f"{structured_validation_feedback(exc)}",
                        tuple(generations),
                    ) from exc
                messages.extend(
                    [
                        {"role": "assistant", "content": generation.content},
                        {
                            "role": "user",
                            "content": structured_validation_feedback(exc),
                        },
                    ]
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

    def _create_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> GenerationResult:
        started = monotonic()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
        except Exception as exc:
            raise ProviderCallError(
                self.provider_name,
                classify_provider_exception(exc),
            ) from exc
        latency_ms = (monotonic() - started) * 1000
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        input_tokens = self._integer_attr(usage, "prompt_tokens")
        output_tokens = self._integer_attr(usage, "completion_tokens")
        request_id = getattr(response, "id", None)
        finish_reason = getattr(choice, "finish_reason", None)
        return GenerationResult(
            content=choice.message.content or "",
            provider=self.provider_name,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            request_id=request_id if isinstance(request_id, str) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )

    @staticmethod
    def _integer_attr(obj: Any, name: str) -> int:
        value = getattr(obj, name, 0) if obj is not None else 0
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def _parse_candidates(
        self,
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
