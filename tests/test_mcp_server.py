import asyncio
from unittest.mock import patch

from starlette.responses import PlainTextResponse

from app.mcp_server import BearerTokenMiddleware, _parse_date, _period


async def _ok_app(scope, receive, send):
    await PlainTextResponse("ok")(scope, receive, send)


def _scope(token: str | None = None):
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 443),
    }


def _request(token: str | None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(BearerTokenMiddleware(_ok_app)(_scope(token), receive, send))
    return messages


def test_bearer_token_middleware_rejects_missing_token():
    with patch("app.mcp_server.settings.gpt_action_token", "expected"):
        messages = _request(None)
    assert messages[0]["status"] == 401


def test_bearer_token_middleware_accepts_configured_token():
    with patch("app.mcp_server.settings.gpt_action_token", "expected"):
        messages = _request("expected")
    assert messages[0]["status"] == 200


def test_period_validation_and_date_parsing():
    assert _parse_date("2026-09-16", "date_from").isoformat() == "2026-09-16"
    assert _period(30, None, None) == (30, None, None)


def test_period_rejects_out_of_range_days():
    try:
        _period(0, None, None)
    except ValueError as error:
        assert "between 1 and 3650" in str(error)
    else:
        raise AssertionError("Expected invalid days to raise ValueError")
