from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import app.services.webhooks as webhooks_module
from app.services.webhooks import send_payment_webhook

URL = "https://example.com/webhook"


@respx.mock
async def test_webhook_success_first_attempt():
    route = respx.post(URL).mock(return_value=httpx.Response(200))

    await send_payment_webhook(
        webhook_url=URL,
        payment_id="payment-1",
        status="succeeded",
        processed_at="2026-10-09T07:49:02+00:00",
    )

    assert route.call_count == 1


@respx.mock
async def test_webhook_retries_and_succeeds(monkeypatch):
    sleep_mock = AsyncMock()

    monkeypatch.setattr(
        webhooks_module.asyncio,
        "sleep",
        sleep_mock,
    )

    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(502),
            httpx.Response(200),
        ]
    )

    await send_payment_webhook(
        webhook_url=URL,
        payment_id="payment-1",
        status="succeeded",
        processed_at="2026-10-09T07:49:02+00:00",
    )

    assert route.call_count == 3
    assert [call.args[0] for call in sleep_mock.await_args_list] == [1, 2]


@respx.mock
async def test_webhook_fails_after_three_attempts(monkeypatch):
    sleep_mock = AsyncMock()

    monkeypatch.setattr(
        webhooks_module.asyncio,
        "sleep",
        sleep_mock,
    )

    route = respx.post(URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await send_payment_webhook(
            webhook_url=URL,
            payment_id="payment-1",
            status="failed",
            processed_at="2026-10-09T07:49:02+00:00",
        )

    assert route.call_count == 3
    assert [call.args[0] for call in sleep_mock.await_args_list] == [1, 2]
