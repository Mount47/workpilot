"""OpenAI-compatible provider — works with OpenAI, DeepSeek, Qwen, GLM, etc."""

import json
import os
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from workpilot.providers.base import EvidenceCandidate, EvidenceType, LLMProvider

EVIDENCE_EXTRACTION_SYSTEM = """You are an evidence extraction engine for project reports.
Given a source file from a project workspace, identify factual statements that serve as evidence for a weekly status report.

Rules:
- Extract ONLY statements that are explicitly written in the file. Never infer or paraphrase.
- Each quote must be an EXACT substring of the source file content.
- Classify each piece of evidence by type: progress, decision, risk, blocker, action_item, context, requirement_change.
- Include the exact line numbers (1-indexed) where the quote appears.
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

Content:
{content}

Extract evidence relevant to the goal. Return a JSON array."""


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider supporting any API with the same interface."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str | None = None,
        max_retries: int = 1,
        timeout: float = 60.0,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            for env_var in ["DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "GLM_API_KEY"]:
                resolved_key = os.environ.get(env_var, "")
                if resolved_key:
                    break

        self.model = model
        self.max_retries = max_retries
        self.client = OpenAI(
            api_key=resolved_key,
            base_url=base_url,
            timeout=timeout,
        )

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        schema = response_model.model_json_schema()
        messages: list[dict[str, str]] = []
        full_system = (
            f"Respond with JSON matching this schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        if system_prompt:
            full_system = system_prompt + "\n\n" + full_system
        messages.append({"role": "system", "content": full_system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(1 + self.max_retries):
            raw = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            text = raw.choices[0].message.content or ""
            text = self._strip_markdown_fences(text)

            try:
                data = json.loads(text)
                return response_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError):
                if attempt == self.max_retries:
                    raise ValueError(
                        f"Failed to parse structured response after {1 + self.max_retries} attempts. "
                        f"Raw: {text[:200]}"
                    )

        raise RuntimeError("Unreachable")

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> list[EvidenceCandidate]:
        if not content.strip():
            return []

        user_prompt = EVIDENCE_EXTRACTION_USER.format(
            goal=goal, file_path=file_path, content=content
        )

        for attempt in range(1 + self.max_retries):
            try:
                raw_text = self.generate_text(
                    prompt=user_prompt,
                    system_prompt=EVIDENCE_EXTRACTION_SYSTEM,
                    temperature=0.0,
                )
                raw_text = self._strip_markdown_fences(raw_text)
                items = json.loads(raw_text)

                if not isinstance(items, list):
                    items = []

                return self._parse_candidates(items, file_path)
            except (json.JSONDecodeError, KeyError, TypeError):
                if attempt == self.max_retries:
                    return []

        return []

    def _parse_candidates(
        self, items: list[dict[str, Any]], file_path: str
    ) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        valid_types = {t.value for t in EvidenceType}

        for item in items:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote", "")
            if not quote:
                continue

            ev_type = item.get("evidence_type", "context")
            if ev_type not in valid_types:
                ev_type = EvidenceType.CONTEXT

            try:
                candidates.append(
                    EvidenceCandidate(
                        evidence_id="",
                        source_file=file_path,
                        quote=quote,
                        start_line=int(item.get("start_line", 1)),
                        end_line=int(item.get("end_line", 1)),
                        evidence_type=ev_type,
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
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
