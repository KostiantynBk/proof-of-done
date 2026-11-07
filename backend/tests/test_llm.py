import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pod import llm
from pydantic import BaseModel, ValidationError


class Answer(BaseModel):
    number: int


@pytest.fixture(autouse=True)
def reset_budget(monkeypatch):
    monkeypatch.setattr(llm, "_budget", llm._DailyBudget())


def settings(**overrides):
    values = {"inference_base_url": "https://example.invalid/v1", "inference_api_key": "test-secret",
                  "models": {"auditor": "model-a"}, "daily_token_budget": 100_000, "fake_llm": False}
    values.update(overrides)
    return SimpleNamespace(**values)


def reply(text, prompt=3, completion=2):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                      "usage": {"prompt_tokens": prompt,
                                                "completion_tokens": completion}})


def run_with(handler, operation, config=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await operation(llm.VultrLLM(config or settings(), client))
    return asyncio.run(run())


def test_fallback_repair_and_per_model_cache():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) <= 2:
            return httpx.Response(400, json={"error": "response_format not supported"})
        if len(requests) == 3:
            return reply('{"number":"wrong"}')
        return reply('Here is JSON: {"number": 42} trailing')

    async def operation(client):
        first = await client.chat("auditor", [{"role": "user", "content": "hello"}], Answer)
        second = await client.chat("auditor", [{"role": "user", "content": "again"}], Answer)
        return first, second

    first, second = run_with(handler, operation)
    assert [r.get("response_format", {}).get("type") for r in requests] == [
        "json_schema", "json_object", None, None, None]
    assert "JSON Schema" in requests[2]["messages"][0]["content"]
    assert "validation errors" in requests[3]["messages"][-1]["content"]
    assert first.value.number == second.value.number == 42
    assert first.record.attempts == 4
    assert first.record.prompt_tokens == 6
    assert first.record.completion_tokens == 4
    assert first.record.strategy == "text"
    assert len(first.record.prompt_sha256) == len(first.record.response_sha256) == 64


def test_schema_success_and_fenced_json():
    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"] is True
        return reply('```json\n{"number": 7}\n```')

    result = run_with(handler, lambda c: c.chat("auditor", [], Answer))
    assert result.value.number == 7
    assert result.record.strategy == "json_schema"


def test_diagnostics_are_opt_in_bounded_and_hide_validation_inputs(caplog):
    seen = []
    secret = 'PRIVATE-DATA-EXAMPLE'
    calls = []

    def handler(_):
        calls.append(1)
        body = json.dumps({'number': secret})
        return httpx.Response(200, json={'choices': [{'message': {'content': body}, 'finish_reason': 'length'}],
                                        'usage': {'prompt_tokens': 3, 'completion_tokens': 2,
                                                  'completion_tokens_details': {'reasoning_tokens': 2}}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = llm.VultrLLM(settings(), client, diagnostic_sink=seen.append)
            with pytest.raises(llm.LLMError, match='after repair'):
                await adapter.chat('auditor', [], Answer)

    asyncio.run(run())
    assert len(calls) == len(seen) == 2
    assert all(x['finish_reason'] == 'length' and not x['valid'] and x['reasoning_tokens'] == 2 for x in seen)
    assert seen[0]['validation_errors'] == [{'loc': ['number'], 'type': 'int_parsing'}]
    assert [x['repair'] for x in seen] == [False, True]
    assert secret not in json.dumps(seen) + caplog.text
    assert 'test-secret' not in json.dumps(seen) + caplog.text
    assert llm._budget.used == 10


def test_diagnostic_sink_failure_does_not_change_result():
    def sink(_):
        raise RuntimeError('diagnostic failure')

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: reply('{"number": 1}'))) as client:
            return await llm.VultrLLM(settings(), client, diagnostic_sink=sink).chat('auditor', [], Answer)

    assert asyncio.run(run()).value.number == 1
    assert llm._budget.used == 5


def test_diagnostic_location_masks_untrusted_fields_and_inputs():
    class StrictAnswer(BaseModel):
        model_config = {'extra': 'forbid'}
        number: int

    try:
        StrictAnswer.model_validate({'number': 1, 'PRIVATE-DATA-EXAMPLE': 'test-secret'})
    except ValidationError as exc:
        result = llm._validation_locations(exc, StrictAnswer.model_json_schema())
    assert result == [{'loc': ['<field>'], 'type': 'extra_forbidden'}]
    assert 'PRIVATE-DATA-EXAMPLE' not in str(result) and 'test-secret' not in str(result)


def test_repair_only_once():
    calls = []
    def handler(request):
        calls.append(request)
        return reply('{"number": "bad"}')
    with pytest.raises(llm.LLMError, match="after repair"):
        run_with(handler, lambda c: c.chat("auditor", [], Answer))
    assert len(calls) == 2


def test_retry_and_no_key_in_error_or_logs(monkeypatch, caplog):
    calls = []
    async def no_sleep(_):
        pass
    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    def handler(request):
        calls.append(request)
        return httpx.Response(500, text="test-secret")
    with pytest.raises(llm.LLMError) as error:
        run_with(handler, lambda c: c.chat("auditor", [], max_tokens=10))
    assert len(calls) == 4
    assert "test-secret" not in str(error.value) + caplog.text


def test_timeout_retry_and_budget():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("test-secret", request=request)
    async def no_sleep(_):
        pass
    original_sleep = llm.asyncio.sleep
    llm.asyncio.sleep = no_sleep
    try:
        with pytest.raises(llm.BudgetExceeded):
            run_with(handler, lambda c: c.chat("auditor", [], max_tokens=10),
                     settings(daily_token_budget=12))
    finally:
        llm.asyncio.sleep = original_sleep
    assert len(calls) == 1


def test_budget_is_shared_and_success_usage_is_charged():
    def handler(_):
        return reply("ok", prompt=5, completion=5)
    async def operation(client):
        await client.chat("auditor", [], max_tokens=10)
        return await client.chat("auditor", [], max_tokens=10)
    with pytest.raises(llm.BudgetExceeded):
        run_with(handler, operation, settings(daily_token_budget=20))


def test_fake_and_get_client():
    fake = llm.FakeLLM({"auditor": [{"number": 4}, "text"]})
    assert asyncio.run(fake.chat("auditor", [], Answer)).value.number == 4
    assert asyncio.run(fake.chat("auditor", [])).value == "text"
    with pytest.raises(llm.LLMError, match="No scripted"):
        asyncio.run(fake.chat("auditor", []))
    assert isinstance(llm.get_llm(settings(fake_llm=True)), llm.FakeLLM)
    assert isinstance(llm.get_llm(settings()), llm.VultrLLM)
    with pytest.raises(llm.LLMError, match="VULTR"):
        llm.get_llm(settings(inference_api_key=""))


def test_balanced_object_ignores_braces_in_strings():
    assert llm._extract_json('before {"a":"} { \\" hi", "b":{"x":1}} after') == (
        '{"a":"} { \\" hi", "b":{"x":1}}')


def test_total_only_usage_is_charged_and_recorded():
    def handler(_):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"total_tokens": 42}})
    async def operation(client):
        result = await client.chat("auditor", [], max_tokens=10)
        assert result.record.total_tokens == 42
        assert result.record.estimated_tokens == 0
        assert result.record.prompt_tokens == result.record.completion_tokens == 0
        with pytest.raises(llm.BudgetExceeded):
            await client.chat("auditor", [], max_tokens=10)
    run_with(handler, operation, settings(daily_token_budget=20))
    assert llm._budget.used == 42


@pytest.mark.parametrize("usage", [{}, {"prompt_tokens": 2}, {"total_tokens": -1},
                                   {"prompt_tokens": True, "completion_tokens": 1},
                                   {"prompt_tokens": 3, "completion_tokens": 4,
                                    "total_tokens": 5}])
def test_incomplete_or_invalid_usage_is_estimated(usage):
    def handler(_):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": usage})
    result = run_with(handler, lambda c: c.chat("auditor", [], max_tokens=10))
    assert result.record.total_tokens == result.record.estimated_tokens == 12
    assert llm._budget.used == 12


def test_cancelled_in_flight_request_retains_reservation():
    async def run():
        entered = asyncio.Event()
        async def handler(_):
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(llm.VultrLLM(settings(), client).chat(
                "auditor", [], max_tokens=10))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert llm._budget.used == 12
        assert llm._budget.reserved == 0
    asyncio.run(run())


def test_network_failure_and_invalid_envelope_are_conservatively_charged():
    def network_failure(request):
        raise httpx.ReadError("failure", request=request)
    with pytest.raises(llm.LLMError, match="network"):
        run_with(network_failure, lambda c: c.chat("auditor", [], max_tokens=10))
    assert llm._budget.used == 12
    with pytest.raises(llm.LLMError, match="invalid response"):
        run_with(lambda _: httpx.Response(200, json=[]),
                 lambda c: c.chat("auditor", [], max_tokens=10))
    assert llm._budget.used == 24


def test_retry_receipt_includes_estimated_server_error(monkeypatch):
    calls = []
    async def no_sleep(_):
        pass
    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    def handler(request):
        calls.append(request)
        return httpx.Response(500) if len(calls) == 1 else reply("ok")
    result = run_with(handler, lambda c: c.chat("auditor", [], max_tokens=10))
    assert result.record.total_tokens == 17
    assert result.record.estimated_tokens == 12
    assert llm._budget.used == 17
