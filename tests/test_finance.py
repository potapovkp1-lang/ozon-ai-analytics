from datetime import date

from app.services.finance import (
    aggregate_finance_operations,
    buyout_percent,
    operation_fee_breakdown,
    product_group,
    traffic_light,
    transaction_bucket,
    vat_part,
)


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
    assert sku_daily[(date(2026, 8, 20), "101")] == {
        "sales_units": 1, "return_units": 1, "sales_amount": 1500,
        "return_amount": 1000, "ozon_fees": 550, "product_name": "",
    }


def test_vat_and_traffic_lights():
    assert round(vat_part(1220, 22), 2) == 220
    assert traffic_light(85, good=80, warning=60) == "green"
    assert traffic_light(70, good=80, warning=60) == "yellow"
    assert traffic_light(50, good=80, warning=60) == "red"
    assert traffic_light(4, good=5, warning=10, inverse=True) == "green"


def test_product_group_prefers_explicit_value_and_avoids_guessing():
    assert product_group("Куртка детская") == "kids"
    assert product_group("Платье женское") == "women"
    assert product_group("Брюки", "мужское") == "men"
    assert product_group("Брюки") == "unknown"


def test_service_adjustments_do_not_turn_into_returned_units():
    operations = [{
        "operation_date": "2026-09-01T10:00:00Z",
        "type": "services",
        "operation_type_name": "Услуги продвижения",
        "accruals_for_sale": -1500,
        "amount": -1500,
        "items": [{"sku": 101, "name": "Брюки"}],
    }]
    daily, sku_daily = aggregate_finance_operations(operations)
    row = daily[date(2026, 9, 1)]
    assert row["sales_units"] == 0
    assert row["return_units"] == 0
    assert row["promotion"] == 1500
    assert sku_daily == {}


def test_ozon_fee_categories_and_transaction_buckets():
    operation = {
        "type": "orders",
        "sale_commission": -400,
        "delivery_charge": -100,
        "services": [
            {"name": "Услуга продвижения", "price": -50},
            {"name": "Эквайринг", "price": -25},
            {"name": "Размещение FBO", "price": -10},
        ],
    }
    assert transaction_bucket(operation) == "sale"
    assert transaction_bucket({"type": "returns"}) == "return"
    assert transaction_bucket({"type": "services", "operation_type_name": "Комиссия"}) is None
    assert operation_fee_breakdown(operation) == {
        "reward": 400,
        "delivery": 100,
        "partner": 25,
        "fbo": 10,
        "promotion": 50,
        "other": 0,
    }


def test_buyout_percent_is_net_sold_relative_to_ordered_units():
    assert buyout_percent(100, 82, 7) == 75
    assert buyout_percent(100, 10, 20) == 0
    assert buyout_percent(0, 10, 0) is None


def test_finance_units_are_counted_once_per_posting_and_sku():
    operations = [
        {
            "operation_id": 1,
            "operation_date": "2026-09-01T10:00:00Z",
            "type": "returns",
            "operation_type_name": "Возврат товара",
            "accruals_for_sale": -2000,
            "amount": -2000,
            "posting": {"posting_number": "POST-1"},
            "items": [{"sku": 101, "name": "Брюки"}],
        },
        {
            "operation_id": 2,
            "operation_date": "2026-09-01T10:01:00Z",
            "type": "returns",
            "operation_type_name": "Обратная логистика",
            "amount": -300,
            "posting": {"posting_number": "POST-1"},
            "items": [{"sku": 101, "name": "Брюки"}],
        },
    ]
    daily, sku_daily = aggregate_finance_operations(operations)
    assert daily[date(2026, 9, 1)]["return_units"] == 1
    assert sku_daily[(date(2026, 9, 1), "101")]["return_units"] == 1
