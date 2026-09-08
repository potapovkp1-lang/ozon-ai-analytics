"""Pure helpers for Ozon finance parsing and management-accounting KPIs.

The tax figures produced here are estimates for management reporting. Exact
VAT and profit tax must still be reconciled with supplier invoices, Ozon UPDs
and the accounting system before a tax return is filed.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
from typing import Iterable


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def operation_day(operation: dict) -> date:
    raw = str(operation.get("operation_date") or "")[:10]
    return date.fromisoformat(raw)


def operation_items(operation: dict) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for item in operation.get("items") or []:
        sku = item.get("sku")
        if sku not in (None, ""):
            result.append((str(sku), str(item.get("name") or "")))
    return result


def operation_fees(operation: dict) -> float:
    """Return Ozon deductions as a positive number without double counting."""
    components = [
        number(operation.get("sale_commission")),
        number(operation.get("delivery_charge")),
        number(operation.get("return_delivery_charge")),
    ]
    components.extend(number(service.get("price")) for service in operation.get("services") or [])
    component_cost = sum(abs(value) for value in components if value < 0)

    transaction_type = str(operation.get("type") or "").lower()
    amount = number(operation.get("amount"))
    if transaction_type not in {"orders", "returns"} and amount < 0:
        return max(component_cost, abs(amount))
    return component_cost


EXPENSE_KEYS = ("reward", "delivery", "partner", "fbo", "promotion", "other")


def transaction_bucket(operation: dict) -> str | None:
    """Classify only real sale/return movements; ignore service adjustments."""
    transaction_type = str(operation.get("type") or "").strip().lower()
    operation_type = str(operation.get("operation_type") or operation.get("operation_type_name") or "").lower()
    if any(token in operation_type for token in ("return", "refund", "возврат")):
        return "return"
    if transaction_type == "returns":
        return "return"
    if any(token in operation_type for token in ("delivered", "sale", "реализац", "продаж")):
        return "sale"
    if transaction_type == "orders":
        return "sale"
    return None


def posting_number(operation: dict) -> str:
    posting = operation.get("posting") or {}
    return str(
        posting.get("posting_number")
        or operation.get("posting_number")
        or operation.get("operation_id")
        or operation.get("id")
        or ""
    )


def expense_category(name: str) -> str:
    normalized = str(name or "").lower()
    if any(token in normalized for token in ("advert", "promotion", "marketing", "реклам", "продвиж", "трафарет")):
        return "promotion"
    if any(token in normalized for token in ("delivery", "logistic", "lastmile", "last mile", "достав", "логист", "магистрал")):
        return "delivery"
    if any(token in normalized for token in ("warehouse", "storage", "fulfillment", "fbo", "хранен", "размещен", "обработк", "сборк")):
        return "fbo"
    if any(token in normalized for token in ("partner", "acquiring", "эквайр", "партн", "пвз", "pickup")):
        return "partner"
    if any(token in normalized for token in ("commission", "вознаграж", "комисси")):
        return "reward"
    return "other"


def operation_fee_breakdown(operation: dict) -> dict[str, float]:
    """Split one Ozon deduction into management-reporting categories."""
    result = {key: 0.0 for key in EXPENSE_KEYS}
    sale_commission = number(operation.get("sale_commission"))
    if sale_commission < 0:
        result["reward"] += abs(sale_commission)
    for field in ("delivery_charge", "return_delivery_charge"):
        value = number(operation.get(field))
        if value < 0:
            result["delivery"] += abs(value)
    for service in operation.get("services") or []:
        value = number(service.get("price"))
        if value < 0:
            result[expense_category(service.get("name") or "")] += abs(value)

    target = operation_fees(operation)
    component_total = sum(result.values())
    if target > component_total:
        label = operation.get("operation_type_name") or operation.get("operation_type") or ""
        result[expense_category(label)] += target - component_total
    elif target and component_total > target:
        scale = target / component_total
        result = {key: value * scale for key, value in result.items()}
    return result


def product_group(product_name: str, configured_group: str = "") -> str:
    """Return a stable reporting group, preferring an explicit SKU setting.

    Product names are only a fallback. Ambiguous names stay unclassified so the
    dashboard never silently assigns adult VAT to a children's product.
    """
    configured = str(configured_group or "").strip().lower()
    aliases = {
        "мужское": "men", "мужской": "men", "men": "men",
        "женское": "women", "женский": "women", "women": "women",
        "детское": "kids", "детский": "kids", "kids": "kids",
    }
    if configured in aliases:
        return aliases[configured]
    name = str(product_name or "").lower()
    if re.search(r"детск|мальчик|девочк|реб[её]н|kids?|junior|baby", name):
        return "kids"
    if re.search(r"женск|для женщин|women|woman|lad(?:y|ies)", name):
        return "women"
    if re.search(r"мужск|для мужчин|\bmen\b|\bman\b", name):
        return "men"
    return "unknown"


def aggregate_finance_operations(operations: Iterable[dict]) -> tuple[dict[date, dict], dict[tuple[date, str], dict]]:
    """Aggregate non-personal daily amounts and SKU unit movements."""
    daily: dict[date, dict] = defaultdict(lambda: {
        "sales_amount": 0.0,
        "return_amount": 0.0,
        "ozon_fees": 0.0,
        "net_payout": 0.0,
        "sales_units": 0,
        "return_units": 0,
        **{key: 0.0 for key in EXPENSE_KEYS},
    })
    sku_daily: dict[tuple[date, str], dict] = defaultdict(lambda: {
        "sales_units": 0, "return_units": 0, "sales_amount": 0.0,
        "return_amount": 0.0, "ozon_fees": 0.0,
    })

    seen_units: set[tuple[str, str, str, int]] = set()
    for operation_index, operation in enumerate(operations):
        try:
            day = operation_day(operation)
        except (TypeError, ValueError):
            continue
        row = daily[day]
        accrual = number(operation.get("accruals_for_sale"))
        bucket = transaction_bucket(operation)
        items = operation_items(operation)
        skus = [sku for sku, _ in items]
        posting = posting_number(operation) or f"row-{operation_index}"
        occurrences: dict[str, int] = defaultdict(int)
        movement_items: list[tuple[str, str, bool]] = []
        for sku, product_name in items:
            occurrences[sku] += 1
            unit_key = (bucket or "none", posting, sku, occurrences[sku])
            is_new_unit = unit_key not in seen_units
            if bucket in {"sale", "return"} and is_new_unit:
                seen_units.add(unit_key)
            movement_items.append((sku, product_name, is_new_unit))
        item_count = len(items)
        amount_per_item = abs(accrual) / item_count if item_count else 0.0
        fee_total = operation_fees(operation)
        fee_per_item = fee_total / item_count if item_count else 0.0

        if bucket == "sale" and accrual > 0:
            row["sales_amount"] += accrual
            row["sales_units"] += sum(1 for _, _, is_new in movement_items if is_new)
            for sku, product_name, is_new in movement_items:
                if is_new:
                    sku_daily[(day, sku)]["sales_units"] += 1
                sku_daily[(day, sku)]["sales_amount"] += amount_per_item
                sku_daily[(day, sku)]["ozon_fees"] += fee_per_item
                sku_daily[(day, sku)]["product_name"] = product_name
        elif bucket == "return" and skus:
            row["return_amount"] += abs(accrual)
            row["return_units"] += sum(1 for _, _, is_new in movement_items if is_new)
            for sku, product_name, is_new in movement_items:
                if is_new:
                    sku_daily[(day, sku)]["return_units"] += 1
                sku_daily[(day, sku)]["return_amount"] += amount_per_item
                sku_daily[(day, sku)]["ozon_fees"] += fee_per_item
                sku_daily[(day, sku)]["product_name"] = product_name

        row["ozon_fees"] += fee_total
        row["net_payout"] += number(operation.get("amount"))
        for key, value in operation_fee_breakdown(operation).items():
            row[key] += value

    # The difference between gross net sales and the sum of all transaction
    # amounts captures commissions, logistics and other Ozon adjustments once.
    # Prefer it to individual service fields, which can overlap in API versions.
    for row in daily.values():
        implied_deductions = row["sales_amount"] - row["return_amount"] - row["net_payout"]
        if implied_deductions >= 0:
            row["ozon_fees"] = implied_deductions
            component_total = sum(row[key] for key in EXPENSE_KEYS)
            if component_total < implied_deductions:
                row["other"] += implied_deductions - component_total
            elif component_total > 0 and component_total > implied_deductions:
                scale = implied_deductions / component_total
                for key in EXPENSE_KEYS:
                    row[key] *= scale

    return dict(daily), dict(sku_daily)


def vat_part(gross: float, rate: float) -> float:
    return gross * rate / (100 + rate) if gross and rate > 0 else 0.0


def traffic_light(value: float | None, *, good: float, warning: float, inverse: bool = False) -> str:
    if value is None:
        return "neutral"
    if inverse:
        return "green" if value <= good else "yellow" if value <= warning else "red"
    return "green" if value >= good else "yellow" if value >= warning else "red"


def trend_status(current: float, previous: float, *, inverse: bool = False) -> str:
    if previous == 0:
        return "green" if current > 0 else "neutral"
    change = (current - previous) / abs(previous) * 100
    if inverse:
        change *= -1
    return "green" if change >= 5 else "yellow" if change >= -5 else "red"


def percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def buyout_percent(ordered_units: int, sold_units: int, return_units: int) -> float | None:
    if ordered_units <= 0:
        return None
    net_sold = max(0, sold_units - return_units)
    return max(0.0, min(100.0, net_sold / ordered_units * 100))
