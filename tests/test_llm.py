import asyncio
import json

import httpx
import pytest

from chessmuse.llm import ConfigurationError, DeepSeekClient, DeepSeekSettings, LLMError, Message


def response_body(text="你好", reason="stop"):
    return {
        "id": "test-request", "model": "deepseek-flash",
        "choices": [{"finish_reason": reason, "message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.fixture(autouse=True)
def isolate_deepseek_environment(monkeypatch):
    # Tests never load the developer's .env or make real HTTP requests.
    import os
    for key in os.environ:
        if key.startswith("DEEPSEEK_"):
            monkeypatch.delenv(key)


def test_env_loading_and_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text('DEEPSEEK_API_KEY="file-test-secret"\nDEEPSEEK_MODEL=deepseek-flash\n', encoding="utf-8-sig")
    settings = DeepSeekSettings.from_env(path)
    assert settings.api_key == "file-test-secret"
    assert settings.model == "deepseek-flash"
    assert settings.thinking == "disabled"
    assert "file-test-secret" not in repr(settings)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-test-secret")
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "128")
    settings = DeepSeekSettings.from_env(path)
    assert settings.api_key == "environment-test-secret"
    assert settings.max_tokens == 128
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekSettings.from_env(path)


@pytest.mark.parametrize("name,value", [
    ("DEEPSEEK_MAX_TOKENS", "zero"), ("DEEPSEEK_MAX_TOKENS", "0"),
    ("DEEPSEEK_TIMEOUT_SECONDS", "nan"), ("DEEPSEEK_TIMEOUT_SECONDS", "0"),
    ("DEEPSEEK_MAX_RETRIES", "-1"), ("DEEPSEEK_THINKING", "yes"),
    ("DEEPSEEK_BASE_URL", "http://example.com"),
    ("DEEPSEEK_BASE_URL", "https://user:password@example.com"),
    ("DEEPSEEK_BASE_URL", "https://example.com?key=test-secret"),
])
def test_invalid_configuration_is_actionable_and_redacted(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configuration-test-secret")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as error:
        DeepSeekSettings.from_env(tmp_path / "absent.env")
    assert name in str(error.value)
    assert "configuration-test-secret" not in str(error.value)
    assert value not in str(error.value)


def test_missing_key_fails_without_network(tmp_path):
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekSettings.from_env(tmp_path / "absent.env")


def run_request(handler, *, settings=None, **kwargs):
    async def run():
        async with DeepSeekClient(settings or DeepSeekSettings(api_key="request-test-secret"),
                                  transport=httpx.MockTransport(handler)) as client:
            return await client.complete([Message("user", "请根据已验证证据讲解")], **kwargs)
    return asyncio.run(run())


def test_request_contract_json_mode_and_usage():
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer request-test-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["stream"] is False
        assert payload["max_tokens"] == 128
        assert payload["response_format"] == {"type": "json_object"}
        assert "JSON" in payload["messages"][0]["content"]
        assert payload["messages"][-1]["content"] == "请根据已验证证据讲解"
        return httpx.Response(200, json=response_body('{"paragraphs":[]}'))

    result = run_request(handler, settings=DeepSeekSettings(api_key="request-test-secret", base_url="https://api.deepseek.com/v1"),
                         json_mode=True, max_tokens=128)
    assert result.data == {"paragraphs": []}
    assert result.usage.total_tokens == 15
    assert result.request_id == "test-request"


def test_plain_text_response():
    def handler(request):
        assert json.loads(request.content)["response_format"] == {"type": "text"}
        return httpx.Response(200, json=response_body())
    result = run_request(handler)
    assert result.text == "你好"
    assert result.data is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_failure_retries_then_succeeds(status, monkeypatch):
    calls, delays = [], []

    async def sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("chessmuse.llm.client.asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, headers={"Retry-After": "2"}, text="private server diagnostic")
        return httpx.Response(200, json=response_body())

    assert run_request(handler).text == "你好"
    assert len(calls) == 2
    assert delays == [2]


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 307])
def test_permanent_http_failure_is_not_retried_or_redirected(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="request-test-secret", headers={"Location": "https://other.example.com"})

    with pytest.raises(LLMError) as error:
        run_request(handler)
    assert error.value.status_code == status
    assert "request-test-secret" not in str(error.value)
    assert len(calls) == 1


def test_network_failure_retries_with_finite_limit(monkeypatch):
    calls = []

    async def sleep(seconds):
        pass

    monkeypatch.setattr("chessmuse.llm.client.asyncio.sleep", sleep)

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("request-test-secret", request=request)

    with pytest.raises(LLMError) as error:
        run_request(handler)
    assert error.value.kind == "network"
    assert "request-test-secret" not in str(error.value)
    assert len(calls) == 3


def test_overall_request_deadline():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=response_body())

    with pytest.raises(LLMError) as error:
        run_request(handler, settings=DeepSeekSettings(api_key="timeout-test-secret", timeout_seconds=0.01, max_retries=0))
    assert error.value.kind == "network"


@pytest.mark.parametrize("body,kind", [
    ({}, "response"),
    (response_body("", "stop"), "response"),
    (response_body("partial", "length"), "incomplete"),
    (response_body("blocked", "content_filter"), "incomplete"),
    (response_body("not json"), "json"),
    (response_body("[]"), "json"),
])
def test_invalid_or_incomplete_output_is_rejected(body, kind):
    with pytest.raises(LLMError) as error:
        run_request(lambda request: httpx.Response(200, json=body), json_mode=True)
    assert error.value.kind == kind


def test_server_non_json_body_is_redacted():
    with pytest.raises(LLMError) as error:
        run_request(lambda request: httpx.Response(200, text="request-test-secret"))
    assert error.value.kind == "response"
    assert "request-test-secret" not in str(error.value)
