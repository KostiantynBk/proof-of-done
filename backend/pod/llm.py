"""Vultr chat client. Model output is an untrusted claim, not verified evidence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError


class BudgetExceeded(RuntimeError):
    """The process-wide daily inference budget cannot cover another request."""


class LLMError(RuntimeError):
    """A sanitized inference or output error."""


class LLMCallRecord(BaseModel):
    role: str
    model: str
    prompt_sha256: str
    response_sha256: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int = 0
    estimated_tokens: int = 0
    latency_ms: int
    attempts: int
    strategy: str


@dataclass
class LLMResult:
    value: Any
    raw_text: str
    record: LLMCallRecord


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _DailyBudget:
    """Reserve in-flight requests under a thread lock, and settle reported usage."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.day = ""
        self.used = 0
        self.reserved = 0

    def reserve(self, limit: int, amount: int) -> str:
        with self.lock:
            day = datetime.now(UTC).date().isoformat()
            if self.day != day:
                self.day, self.used, self.reserved = day, 0, 0
            if self.used + self.reserved + amount > limit:
                raise BudgetExceeded("Daily inference token budget exhausted")
            self.reserved += amount
            return day

    def settle(self, day: str, reserved: int, used: int) -> None:
        with self.lock:
            if self.day == day:
                self.reserved -= reserved
                self.used += used


_budget = _DailyBudget()


def _usage(usage: Any, reservation: int) -> tuple[int, int, int, int]:
    """Return known component counts, charged total, and estimated charge.

    Partial or inconsistent components are not presented as a complete breakdown.
    """
    def count(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    if isinstance(usage, dict):
        p = count(usage.get("prompt_tokens"))
        c = count(usage.get("completion_tokens"))
        total = count(usage.get("total_tokens"))
        if p is not None and c is not None and (total is None or total == p + c):
            return p, c, p + c, 0
        if (total is not None and (p is None or p <= total) and (c is None or c <= total)
                and (p is None or c is None or p + c <= total)):
            return 0, 0, total, 0
    return 0, 0, reservation, reservation


def _extract_json(text: str) -> str:
    """Prefer a fenced block; otherwise find the first balanced JSON object."""
    block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if block:
        return block.group(1).strip()
    start = text.find("{")
    if start < 0:
        return text.strip()
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if not depth:
                return text[start : pos + 1]
    return text[start:]


class VultrLLM:
    def __init__(self, settings: Any, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._strategies: dict[str, str] = {}

    async def chat(
        self, role: str, messages: list[dict[str, Any]],
        response_model: type[BaseModel] | None = None,
        temperature: float = 0.2, max_tokens: int = 4096,
    ) -> LLMResult:
        if not self.settings.inference_api_key:
            raise LLMError("Vultr inference API key is not configured")
        if role not in self.settings.models:
            raise LLMError(f"Unknown model role: {role}")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        model = self.settings.models[role]
        strategy = self._strategies.get(model, "json_schema") if response_model else "text"
        initial_messages = [dict(message) for message in messages]
        working_messages = list(initial_messages)
        attempts = prompt_tokens = completion_tokens = total_tokens = estimated_tokens = 0
        started = time.monotonic()
        repaired = False
        schema = response_model.model_json_schema() if response_model else None
        key = self.settings.inference_api_key
        client = self._client or httpx.AsyncClient(timeout=180.0)
        own_client = self._client is None
        try:
            while True:
                effective = list(working_messages)
                if response_model and strategy != "json_schema":
                    effective.insert(0, {"role": "system", "content": (
                        "Return a JSON object matching this JSON Schema: " + _canonical(schema)
                    )})
                payload: dict[str, Any] = {"model": model, "messages": effective,
                                           "temperature": temperature, "max_tokens": max_tokens}
                if strategy == "json_schema":
                    payload["response_format"] = {"type": "json_schema", "json_schema": {
                        "name": response_model.__name__, "schema": schema, "strict": True}}
                elif strategy == "json_object":
                    payload["response_format"] = {"type": "json_object"}
                # Conservative estimate; actual provider usage is always charged when supplied.
                reservation = max_tokens + max(1, len(_canonical(effective).encode("utf-8")))
                retries = 0
                while True:
                    day = _budget.reserve(self.settings.daily_token_budget, reservation)
                    # Once post is entered, failure or cancellation may still be billable.
                    charged = reservation
                    estimated = reservation
                    attempts += 1
                    try:
                        response = await client.post(
                            self.settings.inference_base_url.rstrip("/") + "/chat/completions",
                            headers={"Authorization": "Bearer " + key}, json=payload, timeout=180.0,
                        )
                        if (response.status_code == 400
                                and strategy in ("json_schema", "json_object")
                                and "response_format" in response.text.lower()):
                            charged = estimated = 0
                            strategy = "json_object" if strategy == "json_schema" else "text"
                            self._strategies[model] = strategy
                            break
                        if response.status_code == 429 or response.status_code >= 500:
                            if response.status_code == 429:
                                charged = estimated = 0
                            if retries < 3:
                                retries += 1
                                await asyncio.sleep(0.25 * 2 ** (retries - 1))
                                continue
                            raise LLMError(f"Inference unavailable (HTTP {response.status_code})")
                        if response.is_error:
                            charged = estimated = 0
                            raise LLMError(f"Inference request failed (HTTP {response.status_code})")
                        try:
                            data = response.json()
                            if not isinstance(data, dict):
                                raise TypeError("invalid response envelope")
                            p, c, charged, estimated = _usage(data.get("usage"), reservation)
                            prompt_tokens += p
                            completion_tokens += c
                            raw = data["choices"][0]["message"]["content"]
                            if not isinstance(raw, str):
                                raise TypeError("non-text response")
                        except (ValueError, TypeError, KeyError, IndexError):
                            raise LLMError("Inference returned an invalid response") from None
                    except httpx.TimeoutException:
                        if retries < 3:
                            retries += 1
                            # A timeout may have consumed provider tokens; charge the reservation.
                            charged = reservation
                            await asyncio.sleep(0.25 * 2 ** (retries - 1))
                            continue
                        charged = reservation
                        raise LLMError("Inference request timed out") from None
                    except httpx.HTTPError:
                        raise LLMError("Inference network error") from None
                    finally:
                        _budget.settle(day, reservation, charged)
                        total_tokens += charged
                        estimated_tokens += estimated
                    try:
                        value = response_model.model_validate_json(_extract_json(raw)) if response_model else raw
                    except (ValidationError, ValueError) as exc:
                        if repaired:
                            raise LLMError("Inference output failed schema validation after repair") from None
                        repaired = True
                        error = str(exc).replace(key, "[redacted]")
                        working_messages = initial_messages + [
                            {"role": "assistant", "content": raw.replace(key, "[redacted]")},
                            {"role": "user", "content": "Correct the JSON validation errors: " + error},
                        ]
                        break
                    if response_model:
                        self._strategies[model] = strategy
                    return LLMResult(value=value, raw_text=raw, record=LLMCallRecord(
                        role=role, model=model, prompt_sha256=_sha(_canonical(effective)),
                        response_sha256=_sha(raw), prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens, total_tokens=total_tokens,
                        estimated_tokens=estimated_tokens,
                        latency_ms=round((time.monotonic() - started) * 1000),
                        attempts=attempts, strategy=strategy,
                    ))
        finally:
            if own_client:
                await client.aclose()


class FakeLLM:
    """Script role responses as strings, dictionaries or Pydantic objects, consumed in order."""

    def __init__(self, responses: dict[str, list[Any]] | None = None) -> None:
        self.responses = {role: list(values) for role, values in (responses or {}).items()}

    async def chat(
        self, role: str, messages: list[dict[str, Any]],
        response_model: type[BaseModel] | None = None,
        temperature: float = 0.2, max_tokens: int = 4096,
    ) -> LLMResult:
        del temperature, max_tokens
        if not self.responses.get(role):
            raise LLMError(f"No scripted fake response for role: {role}")
        item = self.responses[role].pop(0)
        if isinstance(item, BaseModel):
            raw = item.model_dump_json()
        elif isinstance(item, str):
            raw = item
        else:
            raw = _canonical(item)
        value = response_model.model_validate_json(_extract_json(raw)) if response_model else raw
        return LLMResult(value, raw, LLMCallRecord(
            role=role, model="fake", prompt_sha256=_sha(_canonical(messages)),
            response_sha256=_sha(raw), prompt_tokens=0, completion_tokens=0,
            latency_ms=0, attempts=1, strategy="fake"))


def get_llm(settings: Any = None) -> VultrLLM | FakeLLM:
    if settings is None:
        from pod.config import Settings
        settings = Settings()
    if settings.fake_llm:
        return FakeLLM()
    if not settings.inference_api_key:
        raise LLMError("Set VULTR_INFERENCE_API_KEY or enable POD_FAKE_LLM=1")
    return VultrLLM(settings)


# A concise public name for callers that do not care about the transport.
LLMClient = VultrLLM
