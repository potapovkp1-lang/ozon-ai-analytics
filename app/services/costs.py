"""Validated CSV import for historical per-SKU purchase costs."""
from __future__ import annotations

import csv
import io
from datetime import date


HEADERS = [
    "ozon_sku", "артикул", "название", "себестоимость_с_ндс",
    "ндс_поставщика", "доп_затраты_без_ндс", "ндс_продажи", "действует_с",
]

ALIASES = {
    "ozon_sku": ("ozon_sku", "sku_ozon", "sku"),
    "offer_id": ("offer_id", "артикул"),
    "product_name": ("product_name", "название"),
    "purchase_cost_with_vat": ("purchase_cost_with_vat", "себестоимость_с_ндс"),
    "purchase_vat_rate": ("purchase_vat_rate", "ндс_поставщика"),
    "extra_cost_without_vat": ("extra_cost_without_vat", "доп_затраты_без_ндс"),
    "sale_vat_rate": ("sale_vat_rate", "ндс_продажи"),
    "valid_from": ("valid_from", "действует_с"),
}


def template_csv(products: list[dict] | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(HEADERS)
    if products:
        for product in products:
            writer.writerow([
                product["ozon_sku"], product.get("offer_id") or "", product.get("product_name") or "",
                product.get("purchase_cost_with_vat") or "",
                product.get("purchase_vat_rate") if product.get("purchase_vat_rate") is not None else "22",
                product.get("extra_cost_without_vat") if product.get("extra_cost_without_vat") is not None else "0",
                product.get("sale_vat_rate") if product.get("sale_vat_rate") is not None else "22",
                product.get("valid_from") or date.today().replace(month=1, day=1),
            ])
    return "\ufeff" + buffer.getvalue()


def _value(row: dict, key: str, default: str = "") -> str:
    normalized = {str(name or "").strip().lower(): str(value or "").strip() for name, value in row.items()}
    for alias in ALIASES[key]:
        if alias in normalized:
            return normalized[alias]
    return default


def _decimal(raw: str, field: str, row_number: int, *, default: float | None = None) -> float:
    clean = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not clean and default is not None:
        return default
    try:
        return float(clean)
    except ValueError as exc:
        raise ValueError(f"Строка {row_number}: неверное число в поле «{field}»") from exc


def parse_cost_csv(raw: bytes, *, default_purchase_vat: float = 22, default_sale_vat: float = 22) -> list[dict]:
    if len(raw) > 5_000_000:
        raise ValueError("Файл больше 5 МБ")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Сохраните CSV в кодировке UTF-8") from exc
    if not text.strip():
        raise ValueError("CSV-файл пуст")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for row_number, source in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in source.values()):
            continue
        sku = _value(source, "ozon_sku")
        if not sku:
            raise ValueError(f"Строка {row_number}: не заполнен ozon_sku")
        purchase_cost = _decimal(_value(source, "purchase_cost_with_vat"), "себестоимость_с_ндс", row_number)
        if purchase_cost <= 0:
            raise ValueError(f"Строка {row_number}: себестоимость должна быть больше нуля")
        purchase_vat = _decimal(_value(source, "purchase_vat_rate"), "ндс_поставщика", row_number, default=default_purchase_vat)
        sale_vat = _decimal(_value(source, "sale_vat_rate"), "ндс_продажи", row_number, default=default_sale_vat)
        if purchase_vat not in {0, 10, 20, 22} or sale_vat not in {0, 10, 20, 22}:
            raise ValueError(f"Строка {row_number}: ставка НДС должна быть 0, 10, 20 или 22")
        valid_raw = _value(source, "valid_from")
        try:
            valid_from = date.fromisoformat(valid_raw)
        except ValueError as exc:
            raise ValueError(f"Строка {row_number}: дата должна быть в формате ГГГГ-ММ-ДД") from exc
        rows.append({
            "ozon_sku": sku,
            "offer_id": _value(source, "offer_id"),
            "product_name": _value(source, "product_name"),
            "purchase_cost_with_vat": purchase_cost,
            "purchase_vat_rate": purchase_vat,
            "extra_cost_without_vat": _decimal(_value(source, "extra_cost_without_vat"), "доп_затраты_без_ндс", row_number, default=0),
            "sale_vat_rate": sale_vat,
            "valid_from": valid_from,
        })
        if len(rows) > 50_000:
            raise ValueError("В одном файле допускается не более 50 000 строк")
    if not rows:
        raise ValueError("В CSV нет строк с товарами")
    return rows
