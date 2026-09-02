from datetime import date

from app.services.finance import aggregate_finance_operations, traffic_light, vat_part


def test_aggregate_sales_returns_fees_and_skus():
    operations = [
        {
            "operation_date": "2026-08-20T10:00:00Z",
            "type": "orders",
            "accruals_for_sale": 3000,
            "amount": 2200,
            "sale_commission": -450,
            "services": [{"name": "delivery", "price": -350}],
            "items": [{"sku": 101}, {"sku": 102}],
        },
        {
            "operation_date": "2026-08-20T12:00:00Z",
            "type": "returns",
            "accruals_for_sale": -1000,
            "amount": -1150,
            "return_delivery_charge": -150,
            "items": [{"sku": 101}],
        },
    ]
    daily, sku_daily = aggregate_finance_operations(operations)
    row = daily[date(2026, 8, 20)]
    assert row["sales_amount"] == 3000
    assert row["return_amount"] == 1000
    assert row["ozon_fees"] == 950
    assert row["sales_units"] == 2
    assert row["return_units"] == 1
    assert sku_daily[(date(2026, 8, 20), "101")] == {"sales_units": 1, "return_units": 1, "product_name": ""}


def test_vat_and_traffic_lights():
    assert round(vat_part(1220, 22), 2) == 220
    assert traffic_light(85, good=80, warning=60) == "green"
    assert traffic_light(70, good=80, warning=60) == "yellow"
    assert traffic_light(50, good=80, warning=60) == "red"
    assert traffic_light(4, good=5, warning=10, inverse=True) == "green"
