from datetime import date
import asyncio

from app.clients.ozon_seller import OzonSellerClient
from app.services.sync import finance_chunks, metric_number, product_catalog_row, report_day


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
    assert captured["metrics"] == ["revenue", "ordered_units", "delivered_units", "returns", "cancellations"]
    assert "dimension" not in captured


def test_product_catalog_row_extracts_article_size_and_barcode():
    row = product_catalog_row({
        "sku": 123456,
        "offer_id": "ART-42",
        "name": "Брюки мужские",
        "barcodes": ["4600000000001"],
        "attributes": [
            {"name": "Размер", "values": [{"value": "42"}]},
            {"name": "Размер упаковки", "values": [{"value": "30 × 20"}]},
        ],
    })
    assert row == {
        "ozon_sku": "123456", "offer_id": "ART-42",
        "product_name": "Брюки мужские", "size": "42",
        "barcode": "4600000000001",
    }


def test_sku_analytics_requests_day_and_sku_dimensions():
    captured = {}
    client = OzonSellerClient()

    async def fake_post(path, payload):
        captured["path"] = path
        captured.update(payload)
        return {"result": {"data": []}}

    client.post = fake_post
    asyncio.run(client.sku_analytics(date(2026, 8, 1), date(2026, 8, 2)))
    assert captured["path"] == "/v1/analytics/data"
    assert captured["dimensions"] == ["day", "sku"]
    assert captured["metrics"][:5] == ["revenue", "ordered_units", "delivered_units", "returns", "cancellations"]
    assert "hits_view_search" in captured["metrics"]
    assert "hits_view_pdp" in captured["metrics"]
    assert "hits_tocart_pdp" in captured["metrics"]
    assert len(captured["metrics"]) == 14


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


def test_finance_backfill_starts_with_newest_period():
    chunks = finance_chunks(date(2026, 6, 2), date(2026, 9, 2))
    assert chunks[0] == (date(2026, 8, 3), date(2026, 9, 2))
    assert chunks[-1][0] == date(2026, 6, 2)
    assert all((end - start).days <= 30 for start, end in chunks)
