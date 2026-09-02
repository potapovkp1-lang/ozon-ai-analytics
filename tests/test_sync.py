from datetime import date
import asyncio

from app.clients.ozon_seller import OzonSellerClient
from app.services.sync import metric_number, report_day


def test_parse_report_day_from_ozon_dimension():
    assert report_day({"name": "2026-09-02 00:00:00+00:00"}) == date(2026, 9, 2)


def test_parse_metric_from_object():
    assert metric_number({"value": "12.5"}) == 12.5


def test_daily_analytics_requests_day_dimensions():
    captured = {}
    client = OzonSellerClient()

    async def fake_post(path, payload):
        captured.update(payload)
        return {"result": {"data": []}}

    client.post = fake_post
    asyncio.run(client.daily_analytics(date(2026, 8, 1), date(2026, 8, 2)))
    assert captured["dimensions"] == ["day"]
    assert "dimension" not in captured


def test_finance_transactions_request_all_operations():
    captured = {}
    client = OzonSellerClient()

    async def fake_post(path, payload):
        captured["path"] = path
        captured.update(payload)
        return {"result": {"operations": [], "page_count": 1}}

    client.post = fake_post
    asyncio.run(client.finance_transactions(date(2026, 8, 1), date(2026, 8, 31)))
    assert captured["path"] == "/v3/finance/transaction/list"
    assert captured["filter"]["transaction_type"] == "all"
    assert captured["page_size"] == 1000
