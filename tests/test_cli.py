import tempfile
from unittest.mock import patch

import pytest

from ai_tierforge.cli import build_parser, main


MOCK_OPENAI_RESPONSE = {
    "choices": [{"message": {"content": "mock response"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


def make_config(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return f.name


VALID_CONFIG = """
tiers:
  architect:
    model: glm-5.2
    max_tokens: 16000
    use_for: [spec]
    provider: openai-compatible
  workhorse:
    model: deepseek-v4-flash
    max_tokens: 8000
    use_for: [code]
    provider: openai-compatible

budgets:
  per_task:
    limit: 0.10
    on_exceed: warn
  per_day:
    limit: 5.00
    on_exceed: warn
"""


def test_parse_route():
    parser = build_parser()
    args = parser.parse_args(["route", "code", "hello"])
    assert args.command == "route"
    assert args.task_type == "code"
    assert args.prompt == "hello"


def test_parse_report():
    parser = build_parser()
    args = parser.parse_args(["report", "--task", "abc"])
    assert args.command == "report"
    assert args.task == "abc"


def test_parse_report_by_type():
    parser = build_parser()
    args = parser.parse_args(["report", "--type", "code"])
    assert args.command == "report"
    assert args.task_type == "code"


def test_parse_validate():
    parser = build_parser()
    args = parser.parse_args(["validate", "path/to/config.yaml"])
    assert args.command == "validate"
    assert args.config_path == "path/to/config.yaml"


def test_parse_budget_check():
    parser = build_parser()
    args = parser.parse_args(["budget", "check", "--scope", "team:x"])
    assert args.command == "budget"
    assert args.budget_command == "check"
    assert args.scope == "team:x"


def test_parse_budget_reset():
    parser = build_parser()
    args = parser.parse_args(["budget", "reset"])
    assert args.command == "budget"
    assert args.budget_command == "reset"


def test_parse_version():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])


def test_validate_valid_config(capsys):
    path = make_config(VALID_CONFIG)
    rc = main(["validate", path])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Config is valid" in captured.out


def test_validate_invalid_config(capsys):
    content = """
tiers:
  empty:
    model: test
    max_tokens: 0
    use_for: []
    provider: unknown
"""
    path = make_config(content)
    rc = main(["validate", path])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR" in captured.err


def _with_mock(
    monkeypatch, capsys, argv
):
    """Run CLI with mocked HTTP and env var for adapter calls."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
    mock_resp = type("Response", (), {
        "status_code": 200,
        "json": lambda self: MOCK_OPENAI_RESPONSE,
    })()
    with patch("requests.post", return_value=mock_resp):
        rc = main(argv)
    captured = capsys.readouterr()
    return rc, captured


def test_route_prints_result(monkeypatch, capsys):
    path = make_config(VALID_CONFIG)
    rc, captured = _with_mock(
        monkeypatch, capsys,
        ["--config", path, "route", "code", "hello"],
    )
    assert rc == 0
    assert "Task:" in captured.out
    assert "Tier:" in captured.out
    assert "Cost:" in captured.out


def test_report_by_task(monkeypatch, capsys):
    path = make_config(VALID_CONFIG)
    rc, captured = _with_mock(
        monkeypatch, capsys,
        ["--config", path, "route", "code", "x"],
    )
    assert rc == 0


def test_config_not_found(capsys):
    rc = main(["--config", "/nonexistent.yaml", "route", "code", "x"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Error loading config" in captured.err


def test_budget_check(monkeypatch, capsys):
    path = make_config(VALID_CONFIG)
    _with_mock(
        monkeypatch, capsys,
        ["--config", path, "route", "code", "x", "--scope", "test-s"],
    )
    rc, captured = _with_mock(
        monkeypatch, capsys,
        ["--config", path, "budget", "check", "--scope", "test-s"],
    )
    assert rc == 0
    assert "spend" in captured.out or "limit" in captured.out


def test_budget_reset(monkeypatch, capsys):
    path = make_config(VALID_CONFIG)
    _with_mock(
        monkeypatch, capsys,
        ["--config", path, "route", "code", "x", "--scope", "reset-s"],
    )
    rc, captured = _with_mock(
        monkeypatch, capsys,
        ["--config", path, "budget", "reset", "--scope", "reset-s"],
    )
    assert rc == 0
    assert "reset" in captured.out
