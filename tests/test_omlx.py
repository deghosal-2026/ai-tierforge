from decimal import Decimal

import pytest
import requests

from ai_tierforge.omlx import OMLXAdapter


def test_name():
    adapter = OMLXAdapter()
    assert adapter.name == "omlx"


def test_calculate_cost_zero():
    adapter = OMLXAdapter()
    cost_in, cost_out = adapter.calculate_cost("any-model", 100, 50)
    assert cost_in == Decimal("0")
    assert cost_out == Decimal("0")


def test_call_strips_omlx_prefix(requests_mock):
    adapter = OMLXAdapter(endpoint="http://test:11434")
    requests_mock.post(
        "http://test:11434/v1/chat/completions",
        json={
            "choices": [{"message": {"content": "response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )
    result = adapter.call("omlx:qwen2.5-coder:7b", "hello", 100)
    assert result.success is True
    assert result.response == "response"
    assert result.tokens_in == 10
    assert result.tokens_out == 20
    history = requests_mock.last_request
    assert history.json()["model"] == "qwen2.5-coder:7b"


def test_call_success(requests_mock):
    adapter = OMLXAdapter(endpoint="http://test:11434")
    requests_mock.post(
        "http://test:11434/v1/chat/completions",
        json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        },
    )
    result = adapter.call("omlx:m", "prompt", 200)
    assert result.success is True
    assert result.response == "ok"
    assert result.duration_ms >= 0


def test_call_http_error(requests_mock):
    adapter = OMLXAdapter(endpoint="http://test:11434")
    requests_mock.post(
        "http://test:11434/v1/chat/completions",
        status_code=503,
    )
    result = adapter.call("omlx:m", "prompt", 100)
    assert result.success is False
    assert result.error == "http_503"
    assert result.duration_ms >= 0


def test_call_connection_refused():
    adapter = OMLXAdapter(endpoint="http://127.0.0.1:1")
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get("http://127.0.0.1:1/test", timeout=0.001)

    result = adapter.call("omlx:m", "prompt", 100)
    assert result.success is False
    assert result.error == "connection_refused"
    assert result.duration_ms >= 0
    assert result.attempt == 0


def test_call_timeout(requests_mock):
    adapter = OMLXAdapter(endpoint="http://test:11434", timeout=0.001)
    requests_mock.post(
        "http://test:11434/v1/chat/completions",
        exc=requests.exceptions.Timeout,
    )
    result = adapter.call("omlx:m", "prompt", 100)
    assert result.success is False
    assert result.error == "timeout"
    assert result.duration_ms >= 0
    assert result.attempt == 0


def test_check_available_true(requests_mock):
    adapter = OMLXAdapter(endpoint="http://test:11434")
    requests_mock.get("http://test:11434/api/tags", json={"models": []})
    assert adapter.check_available() is True


def test_check_available_false_on_connection_error(requests_mock):
    adapter = OMLXAdapter(endpoint="http://test:11434")
    requests_mock.get("http://test:11434/api/tags", exc=requests.exceptions.ConnectionError)
    assert adapter.check_available() is False
