import pytest

from app.services.costs import parse_cost_csv, template_csv


def test_parse_russian_cost_template():
    products = [{
        "ozon_sku": "123456789", "offer_id": "ART-001", "product_name": "Пример товара",
        "purchase_cost_with_vat": 1220, "purchase_vat_rate": 22,
        "extra_cost_without_vat": 50, "sale_vat_rate": 22, "valid_from": "2026-01-01",
    }]
    rows = parse_cost_csv(template_csv(products).encode("utf-8"))
    assert rows[0]["ozon_sku"] == "123456789"
    assert rows[0]["purchase_cost_with_vat"] == 1220
    assert rows[0]["purchase_vat_rate"] == 22
    assert rows[0]["sale_vat_rate"] == 22


def test_cost_csv_rejects_missing_sku():
    raw = "ozon_sku;себестоимость_с_ндс;действует_с\n;100;2026-01-01\n".encode()
    with pytest.raises(ValueError, match="ozon_sku"):
        parse_cost_csv(raw)
