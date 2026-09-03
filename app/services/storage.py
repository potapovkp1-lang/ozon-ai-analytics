"""PostgreSQL storage and aggregation for the private Ozon dashboard."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.services.finance import percent_change, traffic_light, trend_status, vat_part


@contextmanager
def connection():
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def initialise() -> None:
    """Apply additive, restart-safe schema migrations."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                day DATE PRIMARY KEY,
                revenue NUMERIC(14, 2) NOT NULL DEFAULT 0,
                ordered_units INTEGER NOT NULL DEFAULT 0,
                canceled_units INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_finance (
                day DATE PRIMARY KEY,
                sales_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                return_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                ozon_fees NUMERIC(14, 2) NOT NULL DEFAULT 0,
                net_payout NUMERIC(14, 2) NOT NULL DEFAULT 0,
                sales_units INTEGER NOT NULL DEFAULT 0,
                return_units INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS finance_sku_daily (
                day DATE NOT NULL,
                ozon_sku TEXT NOT NULL,
                product_name TEXT NOT NULL DEFAULT '',
                sales_units INTEGER NOT NULL DEFAULT 0,
                return_units INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, ozon_sku)
            )
        """)
        cur.execute("ALTER TABLE finance_sku_daily ADD COLUMN IF NOT EXISTS product_name TEXT NOT NULL DEFAULT ''")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sku_costs (
                ozon_sku TEXT NOT NULL,
                valid_from DATE NOT NULL,
                offer_id TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL DEFAULT '',
                purchase_cost_with_vat NUMERIC(14, 2) NOT NULL,
                purchase_vat_rate NUMERIC(6, 2) NOT NULL DEFAULT 22,
                extra_cost_without_vat NUMERIC(14, 2) NOT NULL DEFAULT 0,
                sale_vat_rate NUMERIC(6, 2) NOT NULL DEFAULT 22,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (ozon_sku, valid_from)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_runs (
                id BIGSERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ,
                state TEXT NOT NULL,
                detail TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                source TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'waiting',
                detail TEXT NOT NULL DEFAULT '',
                last_attempt_at TIMESTAMPTZ,
                last_success_at TIMESTAMPTZ
            )
        """)
        conn.commit()


def upsert_daily_metric(day: date, revenue: float, ordered_units: int, canceled_units: int) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO daily_metrics (day, revenue, ordered_units, canceled_units, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (day) DO UPDATE SET
              revenue = EXCLUDED.revenue,
              ordered_units = EXCLUDED.ordered_units,
              canceled_units = EXCLUDED.canceled_units,
              updated_at = now()
        """, (day, revenue, ordered_units, canceled_units))
        conn.commit()


def replace_finance_period(
    date_from: date,
    date_to: date,
    daily: dict[date, dict],
    sku_daily: dict[tuple[date, str], dict],
) -> None:
    """Replace a complete API period so later Ozon corrections are reflected."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM daily_finance WHERE day BETWEEN %s AND %s", (date_from, date_to))
        cur.execute("DELETE FROM finance_sku_daily WHERE day BETWEEN %s AND %s", (date_from, date_to))
        current = date_from
        while current <= date_to:
            row = daily.get(current, {})
            cur.execute("""
                INSERT INTO daily_finance (
                    day, sales_amount, return_amount, ozon_fees, net_payout,
                    sales_units, return_units, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            """, (
                current,
                row.get("sales_amount", 0), row.get("return_amount", 0),
                row.get("ozon_fees", 0), row.get("net_payout", 0),
                row.get("sales_units", 0), row.get("return_units", 0),
            ))
            current += timedelta(days=1)
        if sku_daily:
            cur.executemany("""
                INSERT INTO finance_sku_daily (day, ozon_sku, product_name, sales_units, return_units)
                VALUES (%s, %s, %s, %s, %s)
            """, [
                (day, sku, values.get("product_name", ""), values.get("sales_units", 0), values.get("return_units", 0))
                for (day, sku), values in sku_daily.items()
            ])
        conn.commit()


def finance_has_data() -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM daily_finance) AS present")
        return bool(cur.fetchone()["present"])


def finance_earliest_day() -> date | None:
    """Return the first stored finance day so interrupted backfills can resume."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MIN(day) AS earliest FROM daily_finance")
        row = cur.fetchone()
        return row["earliest"] if row else None


def set_sync_state(source: str, state: str, detail: str = "", *, success: bool = False) -> None:
    """Store safe, user-facing sync diagnostics without API payloads or secrets."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sync_state (source, state, detail, last_attempt_at, last_success_at)
            VALUES (%s, %s, %s, now(), CASE WHEN %s THEN now() ELSE NULL END)
            ON CONFLICT (source) DO UPDATE SET
                state = EXCLUDED.state,
                detail = EXCLUDED.detail,
                last_attempt_at = now(),
                last_success_at = CASE
                    WHEN %s THEN now()
                    ELSE sync_state.last_success_at
                END
        """, (source, state, detail[:300], success, success))
        conn.commit()


def import_cost_rows(rows: Iterable[dict]) -> int:
    prepared = list(rows)
    with connection() as conn, conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO sku_costs (
                ozon_sku, valid_from, offer_id, product_name,
                purchase_cost_with_vat, purchase_vat_rate,
                extra_cost_without_vat, sale_vat_rate, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (ozon_sku, valid_from) DO UPDATE SET
                offer_id = EXCLUDED.offer_id,
                product_name = EXCLUDED.product_name,
                purchase_cost_with_vat = EXCLUDED.purchase_cost_with_vat,
                purchase_vat_rate = EXCLUDED.purchase_vat_rate,
                extra_cost_without_vat = EXCLUDED.extra_cost_without_vat,
                sale_vat_rate = EXCLUDED.sale_vat_rate,
                updated_at = now()
        """, [(
            row["ozon_sku"], row["valid_from"], row["offer_id"], row["product_name"],
            row["purchase_cost_with_vat"], row["purchase_vat_rate"],
            row["extra_cost_without_vat"], row["sale_vat_rate"],
        ) for row in prepared])
        conn.commit()
    return len(prepared)


def cost_template_products() -> list[dict]:
    """Return known Ozon SKUs with their latest cost, ready for CSV export."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            WITH products AS (
                SELECT ozon_sku, MAX(product_name) AS product_name
                FROM finance_sku_daily
                GROUP BY ozon_sku
            )
            SELECT p.ozon_sku, p.product_name,
                   COALESCE(c.offer_id, '') AS offer_id,
                   c.purchase_cost_with_vat::float AS purchase_cost_with_vat,
                   c.purchase_vat_rate::float AS purchase_vat_rate,
                   c.extra_cost_without_vat::float AS extra_cost_without_vat,
                   c.sale_vat_rate::float AS sale_vat_rate,
                   c.valid_from
            FROM products p
            LEFT JOIN LATERAL (
                SELECT * FROM sku_costs c WHERE c.ozon_sku = p.ozon_sku
                ORDER BY c.valid_from DESC LIMIT 1
            ) c ON TRUE
            ORDER BY p.product_name, p.ozon_sku
        """)
        return cur.fetchall()


def _period_rows(cur, date_from: date, date_to: date) -> list[dict]:
    cur.execute("""
        SELECT
            COALESCE(m.day, f.day) AS day,
            COALESCE(m.revenue, 0)::float AS ordered_amount,
            COALESCE(m.ordered_units, 0) AS ordered_units,
            COALESCE(m.canceled_units, 0) AS canceled_units,
            COALESCE(f.sales_amount, 0)::float AS sales_amount,
            COALESCE(f.return_amount, 0)::float AS return_amount,
            COALESCE(f.ozon_fees, 0)::float AS ozon_fees,
            COALESCE(f.net_payout, 0)::float AS net_payout,
            COALESCE(f.sales_units, 0) AS sales_units,
            COALESCE(f.return_units, 0) AS return_units,
            (f.day IS NOT NULL) AS finance_present
        FROM daily_metrics m
        FULL OUTER JOIN daily_finance f ON f.day = m.day
        WHERE COALESCE(m.day, f.day) BETWEEN %s AND %s
        ORDER BY day ASC
    """, (date_from, date_to))
    return cur.fetchall()


def _costs(cur, date_from: date, date_to: date) -> dict:
    cur.execute("""
        WITH movements AS (
            SELECT day, ozon_sku, sales_units, return_units,
                   sales_units - return_units AS net_units
            FROM finance_sku_daily
            WHERE day BETWEEN %s AND %s
        ), priced AS (
            SELECT m.*,
                   c.purchase_cost_with_vat::float AS purchase_gross,
                   c.purchase_vat_rate::float AS purchase_vat_rate,
                   c.extra_cost_without_vat::float AS extra_cost_net,
                   c.sale_vat_rate::float AS sale_vat_rate
            FROM movements m
            LEFT JOIN LATERAL (
                SELECT * FROM sku_costs c
                WHERE c.ozon_sku = m.ozon_sku AND c.valid_from <= m.day
                ORDER BY c.valid_from DESC LIMIT 1
            ) c ON TRUE
        )
        SELECT
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL THEN
                net_units * (purchase_gross + extra_cost_net) ELSE 0 END), 0)::float AS cogs_gross,
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL THEN
                net_units * (purchase_gross / (1 + purchase_vat_rate / 100) + extra_cost_net) ELSE 0 END), 0)::float AS cogs_net,
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL THEN
                net_units * (purchase_gross - purchase_gross / (1 + purchase_vat_rate / 100)) ELSE 0 END), 0)::float AS input_vat,
            COALESCE(SUM(ABS(sales_units) + ABS(return_units)), 0)::int AS movement_units,
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL THEN ABS(sales_units) + ABS(return_units) ELSE 0 END), 0)::int AS priced_units,
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL THEN ABS(net_units) * sale_vat_rate ELSE 0 END), 0)::float AS weighted_vat,
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL AND sale_vat_rate > 0 THEN
                ABS(net_units) * sale_vat_rate / (100 + sale_vat_rate) ELSE 0 END), 0)::float AS weighted_vat_fraction,
            COALESCE(SUM(CASE WHEN purchase_gross IS NOT NULL THEN ABS(net_units) ELSE 0 END), 0)::int AS weighted_units
        FROM priced
    """, (date_from, date_to))
    return cur.fetchone()


def _snapshot(cur, date_from: date, date_to: date) -> dict:
    rows = _period_rows(cur, date_from, date_to)
    costs = _costs(cur, date_from, date_to)
    totals = {
        "ordered_amount": sum(row["ordered_amount"] for row in rows),
        "ordered_units": sum(row["ordered_units"] for row in rows),
        "canceled_units": sum(row["canceled_units"] for row in rows),
        "sales_amount": sum(row["sales_amount"] for row in rows),
        "sales_units": sum(row["sales_units"] for row in rows),
        "return_amount": sum(row["return_amount"] for row in rows),
        "return_units": sum(row["return_units"] for row in rows),
        "ozon_fees": sum(row["ozon_fees"] for row in rows),
        "net_payout": sum(row["net_payout"] for row in rows),
    }
    totals.update(costs)
    totals["rows"] = rows
    return totals


def _delta_note(current: float, previous: float, fallback: str) -> str:
    change = percent_change(current, previous)
    if change is None:
        return fallback
    arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
    return f"{arrow} {abs(change):g}% к прошлому периоду"


def _card(key: str, name: str, value: float | None, unit: str, status: str, note: str, tooltip: str) -> dict:
    return {"key": key, "name": name, "value": None if value is None else round(value, 2), "unit": unit, "status": status, "note": note, "tooltip": tooltip}


def dashboard(days: int = 30, date_from: date | None = None, date_to: date | None = None) -> dict:
    today = date.today()
    end = date_to or (today - timedelta(days=1))
    start = date_from or (end - timedelta(days=max(1, days) - 1))
    if start > end:
        raise ValueError("Дата начала не может быть позже даты окончания")
    period_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)

    with connection() as conn, conn.cursor() as cur:
        current = _snapshot(cur, start, end)
        previous = _snapshot(cur, previous_start, previous_end)
        cur.execute("""
            SELECT MIN(day) AS earliest, MAX(day) AS latest, MAX(updated_at) AS last_sync
            FROM (
                SELECT day, updated_at FROM daily_metrics
                UNION ALL SELECT day, updated_at FROM daily_finance
            ) source
        """)
        availability = cur.fetchone()
        cur.execute("""
            SELECT source, state, detail, last_attempt_at, last_success_at
            FROM sync_state
            ORDER BY source
        """)
        source_rows = cur.fetchall()

    movement_units = current["movement_units"]
    coverage = round(current["priced_units"] / movement_units * 100, 1) if movement_units else 0.0
    costs_ready = movement_units > 0 and coverage >= 99.9
    finance_ready = any(row["finance_present"] for row in current["rows"])

    net_sales_gross = current["sales_amount"] - current["return_amount"]
    previous_net_sales = previous["sales_amount"] - previous["return_amount"]
    blended_vat_rate = current["weighted_vat"] / current["weighted_units"] if current["weighted_units"] else settings.default_sale_vat_rate
    vat_fraction = current["weighted_vat_fraction"] / current["weighted_units"] if current["weighted_units"] else settings.default_sale_vat_rate / (100 + settings.default_sale_vat_rate)
    output_vat = max(net_sales_gross, 0) * vat_fraction
    ozon_input_vat = vat_part(current["ozon_fees"], settings.ozon_service_vat_rate)
    ozon_fees_net = current["ozon_fees"] - ozon_input_vat
    profit_before_tax = income_tax = net_profit = None
    markup_before_tax = markup_after_tax = net_margin = None
    if costs_ready and finance_ready:
        profit_before_tax = net_sales_gross - output_vat - current["cogs_net"] - ozon_fees_net
        income_tax = max(profit_before_tax, 0) * settings.income_tax_rate / 100
        net_profit = profit_before_tax - income_tax
        if current["cogs_net"]:
            markup_before_tax = profit_before_tax / current["cogs_net"] * 100
            markup_after_tax = net_profit / current["cogs_net"] * 100
        sales_net_of_vat = net_sales_gross - output_vat
        if sales_net_of_vat:
            net_margin = net_profit / sales_net_of_vat * 100

    net_sold_units = current["sales_units"] - current["return_units"]
    buyout_rate = max(0.0, min(100.0, net_sold_units / current["ordered_units"] * 100)) if current["ordered_units"] else 0.0
    return_rate = current["return_units"] / current["sales_units"] * 100 if current["sales_units"] else 0.0
    cogs_share = current["cogs_gross"] / net_sales_gross * 100 if net_sales_gross > 0 and costs_ready else None

    period_note = f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"
    costs_note = f"Себестоимость покрывает {coverage:g}% движений" if movement_units else "Загрузите себестоимость по SKU"
    profit_note = f"Рентабельность {net_margin:.1f}%" if net_margin is not None else costs_note

    return_amount_status = "red" if previous["return_amount"] == 0 and current["return_amount"] > 0 else trend_status(current["return_amount"], previous["return_amount"], inverse=True)
    return_units_status = "red" if previous["return_units"] == 0 and current["return_units"] > 0 else trend_status(current["return_units"], previous["return_units"], inverse=True)
    kpis = [
        _card("ordered_amount", "Заказано на сумму", current["ordered_amount"], "₽", trend_status(current["ordered_amount"], previous["ordered_amount"]), _delta_note(current["ordered_amount"], previous["ordered_amount"], period_note), "Стоимость всех оформленных заказов за выбранный период."),
        _card("ordered_units", "Заказано товаров", current["ordered_units"], "шт.", trend_status(current["ordered_units"], previous["ordered_units"]), _delta_note(current["ordered_units"], previous["ordered_units"], period_note), "Количество заказанных единиц, включая те, что позже могли отменить или вернуть."),
        _card("sales_amount", "Продажи", current["sales_amount"] if finance_ready else None, "₽", trend_status(net_sales_gross, previous_net_sales) if finance_ready else "neutral", _delta_note(net_sales_gross, previous_net_sales, "Финансовые операции Ozon"), "Начисления за фактически реализованные товары до вычета возвратов."),
        _card("sales_units", "Продано товаров", current["sales_units"] if finance_ready else None, "шт.", trend_status(current["sales_units"], previous["sales_units"]) if finance_ready else "neutral", _delta_note(current["sales_units"], previous["sales_units"], "По финансовым операциям"), "Количество товаров в операциях реализации Ozon."),
        _card("return_amount", "Возвраты", current["return_amount"] if finance_ready else None, "₽", return_amount_status if finance_ready else "neutral", _delta_note(current["return_amount"], previous["return_amount"], f"Доля возвратов {return_rate:.1f}%"), "Сумма возвратов и сторнированных начислений. Чем меньше, тем лучше."),
        _card("return_units", "Возвращено товаров", current["return_units"] if finance_ready else None, "шт.", return_units_status if finance_ready else "neutral", _delta_note(current["return_units"], previous["return_units"], f"Доля возвратов {return_rate:.1f}%"), "Количество товаров в операциях возврата Ozon."),
        _card("ozon_fees", "Расходы Ozon", current["ozon_fees"] if finance_ready else None, "₽", "neutral" if not finance_ready else traffic_light(current["ozon_fees"] / net_sales_gross * 100 if net_sales_gross > 0 else None, good=25, warning=40, inverse=True), "Комиссии, логистика и услуги", "Все комиссии, логистика, эквайринг и другие удержания Ozon за период."),
        _card("cogs", "Себестоимость продаж", current["cogs_gross"] if costs_ready else None, "₽", traffic_light(cogs_share, good=50, warning=70, inverse=True), costs_note, "Закупочная стоимость с НДС плюс дополнительные затраты без НДС, с учётом возвратов."),
        _card("net_profit", "Чистая прибыль", net_profit, "₽", traffic_light(net_margin, good=15, warning=5), profit_note, "Расчётная прибыль после расходов Ozon, НДС и налога на прибыль. Требует полной себестоимости."),
        _card("buyout_rate", "Процент выкупа", buyout_rate, "%", traffic_light(buyout_rate, good=80, warning=60), f"Продано за вычетом возвратов: {net_sold_units} шт.", "Проданные товары за вычетом возвратов относительно заказанных единиц."),
        _card("markup_before_tax", "Наценка после расходов Ozon", markup_before_tax, "%", traffic_light(markup_before_tax, good=30, warning=10), "До налога на прибыль", "Расчётная прибыль до налога на прибыль, делённая на себестоимость без возмещаемого НДС."),
        _card("markup_after_tax", "Наценка после налогов", markup_after_tax, "%", traffic_light(markup_after_tax, good=20, warning=5), f"НДС ≈ {blended_vat_rate:.1f}%, налог на прибыль {settings.income_tax_rate:g}%", "Чистая прибыль после расчётных налогов, делённая на себестоимость без возмещаемого НДС."),
    ]

    insights = []
    if not finance_ready:
        insights.append({"status": "yellow", "title": "Финансовые данные загружаются", "text": "Продажи, возвраты и расходы Ozon появятся после финансовой синхронизации."})
    if not costs_ready:
        insights.append({"status": "yellow", "title": "Нужна себестоимость", "text": "Загрузите CSV по Ozon SKU, чтобы рассчитать прибыль и наценку."})
    if buyout_rate and buyout_rate < 60:
        insights.append({"status": "red", "title": "Низкий процент выкупа", "text": "Проверьте причины отмен и возвратов по товарам и размерам."})
    if net_profit is not None and net_profit < 0:
        insights.append({"status": "red", "title": "Отрицательная прибыль", "text": "Расходы и себестоимость превышают доход без НДС за выбранный период."})
    if not insights:
        insights.append({"status": "green", "title": "Критичных отклонений нет", "text": "Основные показатели выбранного периода находятся в рабочем диапазоне."})

    return {
        "status": "ready" if current["rows"] else "waiting_for_sync",
        "period_days": period_days,
        "period": {"date_from": start.isoformat(), "date_to": end.isoformat(), "days": period_days, "label": period_note},
        "kpis": kpis,
        "series": [{
            "day": row["day"].isoformat(),
            "ordered_amount": round(row["ordered_amount"], 2),
            "sales_amount": round(row["sales_amount"] - row["return_amount"], 2),
            "return_amount": round(row["return_amount"], 2),
        } for row in current["rows"]],
        "breakdown": {
            "net_sales_gross": round(net_sales_gross, 2),
            "output_vat": round(output_vat, 2),
            "input_vat_cogs": round(current["input_vat"], 2),
            "input_vat_ozon": round(ozon_input_vat, 2),
            "ozon_fees": round(current["ozon_fees"], 2),
            "cogs_net": round(current["cogs_net"], 2) if costs_ready else None,
            "profit_before_tax": round(profit_before_tax, 2) if profit_before_tax is not None else None,
            "income_tax": round(income_tax, 2) if income_tax is not None else None,
            "net_profit": round(net_profit, 2) if net_profit is not None else None,
        },
        "data_quality": {
            "finance_ready": finance_ready,
            "cost_coverage_percent": coverage,
            "tax_estimate": True,
            "earliest_date": availability["earliest"].isoformat() if availability and availability["earliest"] else None,
            "latest_date": availability["latest"].isoformat() if availability and availability["latest"] else None,
            "last_sync": availability["last_sync"].isoformat() if availability and availability["last_sync"] else None,
            "sources": {
                row["source"]: {
                    "state": row["state"],
                    "detail": row["detail"],
                    "last_attempt_at": row["last_attempt_at"].isoformat() if row["last_attempt_at"] else None,
                    "last_success_at": row["last_success_at"].isoformat() if row["last_success_at"] else None,
                }
                for row in source_rows
            },
        },
        "insights": insights,
    }
