"""PostgreSQL storage and aggregation for the private Ozon dashboard."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.services.finance import FINANCING_KEYS, EXPENSE_KEYS, buyout_percent, percent_change, product_group, traffic_light, trend_status, vat_part


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
                delivered_units INTEGER NOT NULL DEFAULT 0,
                returned_units INTEGER NOT NULL DEFAULT 0,
                canceled_units INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS delivered_units INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE daily_metrics ADD COLUMN IF NOT EXISTS returned_units INTEGER NOT NULL DEFAULT 0")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_finance (
                day DATE PRIMARY KEY,
                sales_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                return_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                ozon_fees NUMERIC(14, 2) NOT NULL DEFAULT 0,
                net_payout NUMERIC(14, 2) NOT NULL DEFAULT 0,
                sales_units INTEGER NOT NULL DEFAULT 0,
                return_units INTEGER NOT NULL DEFAULT 0,
                reward NUMERIC(14, 2) NOT NULL DEFAULT 0,
                delivery NUMERIC(14, 2) NOT NULL DEFAULT 0,
                partner NUMERIC(14, 2) NOT NULL DEFAULT 0,
                fbo NUMERIC(14, 2) NOT NULL DEFAULT 0,
                promotion NUMERIC(14, 2) NOT NULL DEFAULT 0,
                other NUMERIC(14, 2) NOT NULL DEFAULT 0,
                loan NUMERIC(14, 2) NOT NULL DEFAULT 0,
                factoring NUMERIC(14, 2) NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        for column in EXPENSE_KEYS:
            cur.execute(f"ALTER TABLE daily_finance ADD COLUMN IF NOT EXISTS {column} NUMERIC(14, 2) NOT NULL DEFAULT 0")
        for column in FINANCING_KEYS:
            cur.execute(f"ALTER TABLE daily_finance ADD COLUMN IF NOT EXISTS {column} NUMERIC(14, 2) NOT NULL DEFAULT 0")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS finance_sku_daily (
                day DATE NOT NULL,
                ozon_sku TEXT NOT NULL,
                product_name TEXT NOT NULL DEFAULT '',
                sales_units INTEGER NOT NULL DEFAULT 0,
                return_units INTEGER NOT NULL DEFAULT 0,
                sales_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                return_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                ozon_fees NUMERIC(14, 2) NOT NULL DEFAULT 0,
                PRIMARY KEY (day, ozon_sku)
            )
        """)
        cur.execute("ALTER TABLE finance_sku_daily ADD COLUMN IF NOT EXISTS product_name TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE finance_sku_daily ADD COLUMN IF NOT EXISTS sales_amount NUMERIC(14, 2) NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE finance_sku_daily ADD COLUMN IF NOT EXISTS return_amount NUMERIC(14, 2) NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE finance_sku_daily ADD COLUMN IF NOT EXISTS ozon_fees NUMERIC(14, 2) NOT NULL DEFAULT 0")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analytics_sku_daily (
                day DATE NOT NULL,
                ozon_sku TEXT NOT NULL,
                product_name TEXT NOT NULL DEFAULT '',
                ordered_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
                ordered_units INTEGER NOT NULL DEFAULT 0,
                delivered_units INTEGER NOT NULL DEFAULT 0,
                returned_units INTEGER NOT NULL DEFAULT 0,
                canceled_units INTEGER NOT NULL DEFAULT 0,
                hits_view_search INTEGER NOT NULL DEFAULT 0,
                hits_view_pdp INTEGER NOT NULL DEFAULT 0,
                hits_view INTEGER NOT NULL DEFAULT 0,
                hits_tocart_search INTEGER NOT NULL DEFAULT 0,
                hits_tocart_pdp INTEGER NOT NULL DEFAULT 0,
                hits_tocart INTEGER NOT NULL DEFAULT 0,
                session_view_search INTEGER NOT NULL DEFAULT 0,
                session_view_pdp INTEGER NOT NULL DEFAULT 0,
                conv_tocart_pdp NUMERIC(8, 3),
                PRIMARY KEY (day, ozon_sku)
            )
        """)
        cur.execute("ALTER TABLE analytics_sku_daily ADD COLUMN IF NOT EXISTS delivered_units INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE analytics_sku_daily ADD COLUMN IF NOT EXISTS returned_units INTEGER NOT NULL DEFAULT 0")
        for column in (
            "hits_view_search", "hits_view_pdp", "hits_view", "hits_tocart_search",
            "hits_tocart_pdp", "hits_tocart", "session_view_search", "session_view_pdp",
        ):
            cur.execute(f"ALTER TABLE analytics_sku_daily ADD COLUMN IF NOT EXISTS {column} INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE analytics_sku_daily ADD COLUMN IF NOT EXISTS conv_tocart_pdp NUMERIC(8, 3)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sku_costs (
                ozon_sku TEXT NOT NULL,
                valid_from DATE NOT NULL,
                offer_id TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL DEFAULT '',
                product_group TEXT NOT NULL DEFAULT '',
                purchase_cost_with_vat NUMERIC(14, 2) NOT NULL,
                purchase_vat_rate NUMERIC(6, 2) NOT NULL DEFAULT 22,
                extra_cost_without_vat NUMERIC(14, 2) NOT NULL DEFAULT 0,
                sale_vat_rate NUMERIC(6, 2) NOT NULL DEFAULT 22,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (ozon_sku, valid_from)
            )
        """)
        cur.execute("ALTER TABLE sku_costs ADD COLUMN IF NOT EXISTS product_group TEXT NOT NULL DEFAULT ''")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_catalog (
                ozon_sku TEXT PRIMARY KEY,
                offer_id TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL DEFAULT '',
                size TEXT NOT NULL DEFAULT '',
                barcode TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_stocks (
                warehouse_name TEXT NOT NULL,
                ozon_sku TEXT NOT NULL,
                offer_id TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL DEFAULT '',
                free_to_sell INTEGER NOT NULL DEFAULT 0,
                reserved INTEGER NOT NULL DEFAULT 0,
                promised INTEGER NOT NULL DEFAULT 0,
                retail_price NUMERIC(14, 2) NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (warehouse_name, ozon_sku)
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


def upsert_daily_metric(
    day: date,
    revenue: float,
    ordered_units: int,
    delivered_units: int,
    returned_units: int,
    canceled_units: int,
) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO daily_metrics (
                day, revenue, ordered_units, delivered_units, returned_units,
                canceled_units, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (day) DO UPDATE SET
              revenue = EXCLUDED.revenue,
              ordered_units = EXCLUDED.ordered_units,
              delivered_units = EXCLUDED.delivered_units,
              returned_units = EXCLUDED.returned_units,
              canceled_units = EXCLUDED.canceled_units,
              updated_at = now()
        """, (day, revenue, ordered_units, delivered_units, returned_units, canceled_units))
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
                    sales_units, return_units, reward, delivery, partner, fbo,
                    promotion, other, loan, factoring, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            """, (
                current,
                row.get("sales_amount", 0), row.get("return_amount", 0),
                row.get("ozon_fees", 0), row.get("net_payout", 0),
                row.get("sales_units", 0), row.get("return_units", 0),
                row.get("reward", 0), row.get("delivery", 0),
                row.get("partner", 0), row.get("fbo", 0),
                row.get("promotion", 0), row.get("other", 0),
                row.get("loan", 0), row.get("factoring", 0),
            ))
            current += timedelta(days=1)
        if sku_daily:
            cur.executemany("""
                INSERT INTO finance_sku_daily (
                    day, ozon_sku, product_name, sales_units, return_units,
                    sales_amount, return_amount, ozon_fees
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, [
                (
                    day, sku, values.get("product_name", ""),
                    values.get("sales_units", 0), values.get("return_units", 0),
                    values.get("sales_amount", 0), values.get("return_amount", 0),
                    values.get("ozon_fees", 0),
                )
                for (day, sku), values in sku_daily.items()
            ])
        conn.commit()


def replace_analytics_sku_period(date_from: date, date_to: date, rows: Iterable[dict]) -> None:
    prepared = list(rows)
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM analytics_sku_daily WHERE day BETWEEN %s AND %s", (date_from, date_to))
        if prepared:
            cur.executemany("""
                INSERT INTO analytics_sku_daily (
                    day, ozon_sku, product_name, ordered_amount, ordered_units,
                    delivered_units, returned_units, canceled_units,
                    hits_view_search, hits_view_pdp, hits_view,
                    hits_tocart_search, hits_tocart_pdp, hits_tocart,
                    session_view_search, session_view_pdp, conv_tocart_pdp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, [(
                row["day"], row["ozon_sku"], row.get("product_name", ""),
                row.get("ordered_amount", 0), row.get("ordered_units", 0),
                row.get("delivered_units", 0), row.get("returned_units", 0),
                row.get("canceled_units", 0),
                row.get("hits_view_search", 0), row.get("hits_view_pdp", 0),
                row.get("hits_view", 0), row.get("hits_tocart_search", 0),
                row.get("hits_tocart_pdp", 0), row.get("hits_tocart", 0),
                row.get("session_view_search", 0), row.get("session_view_pdp", 0),
                row.get("conv_tocart_pdp"),
            ) for row in prepared])
        conn.commit()


def replace_inventory_snapshot(rows: Iterable[dict], prices: dict[str, float]) -> None:
    prepared: list[tuple] = []
    for row in rows:
        sku = str(row.get("sku") or "")
        warehouse = str(row.get("warehouse_name") or "").strip()
        if not sku or not warehouse:
            continue
        offer_id = str(row.get("item_code") or "")
        prepared.append((
            warehouse, sku, offer_id, str(row.get("item_name") or ""),
            int(row.get("free_to_sell_amount") or 0), int(row.get("reserved_amount") or 0),
            int(row.get("promised_amount") or 0), prices.get(offer_id, 0),
        ))
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM warehouse_stocks")
        if prepared:
            cur.executemany("""
                INSERT INTO warehouse_stocks (
                    warehouse_name, ozon_sku, offer_id, product_name,
                    free_to_sell, reserved, promised, retail_price, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            """, prepared)
        conn.commit()


def replace_product_catalog(rows: Iterable[dict]) -> None:
    """Replace the read-only product metadata used by the photo team."""
    prepared = [row for row in rows if str(row.get("ozon_sku") or "")]
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM product_catalog")
        if prepared:
            cur.executemany("""
                INSERT INTO product_catalog (
                    ozon_sku, offer_id, product_name, size, barcode, updated_at
                ) VALUES (%s, %s, %s, %s, %s, now())
            """, [(
                str(row["ozon_sku"]), str(row.get("offer_id") or ""),
                str(row.get("product_name") or ""), str(row.get("size") or ""),
                str(row.get("barcode") or ""),
            ) for row in prepared])
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


def finance_needs_sku_backfill() -> bool:
    """Detect rows created before SKU amounts or fee categories existed."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (SELECT 1 FROM daily_finance WHERE sales_amount > 0) AS has_finance,
                   EXISTS (SELECT 1 FROM finance_sku_daily WHERE sales_amount > 0) AS has_sku_amounts,
                   NOT EXISTS (
                       SELECT 1 FROM sync_state
                       WHERE source = 'finance_units_v3' AND state = 'ready'
                   ) AS missing_unit_deduplication,
                   NOT EXISTS (
                       SELECT 1 FROM sync_state
                       WHERE source = 'finance_funding_v1' AND state = 'ready'
                   ) AS missing_funding_split,
                   EXISTS (
                       SELECT 1 FROM daily_finance
                       WHERE ozon_fees > 0
                         AND reward + delivery + partner + fbo + promotion + other = 0
                   ) AS missing_fee_breakdown
        """)
        row = cur.fetchone()
        return bool(row["has_finance"] and (
            not row["has_sku_amounts"]
            or row["missing_fee_breakdown"]
            or row["missing_unit_deduplication"]
            or row["missing_funding_split"]
        ))


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
                ozon_sku, valid_from, offer_id, product_name, product_group,
                purchase_cost_with_vat, purchase_vat_rate,
                extra_cost_without_vat, sale_vat_rate, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (ozon_sku, valid_from) DO UPDATE SET
                offer_id = EXCLUDED.offer_id,
                product_name = EXCLUDED.product_name,
                product_group = EXCLUDED.product_group,
                purchase_cost_with_vat = EXCLUDED.purchase_cost_with_vat,
                purchase_vat_rate = EXCLUDED.purchase_vat_rate,
                extra_cost_without_vat = EXCLUDED.extra_cost_without_vat,
                sale_vat_rate = EXCLUDED.sale_vat_rate,
                updated_at = now()
        """, [(
            row["ozon_sku"], row["valid_from"], row["offer_id"], row["product_name"], row.get("product_group", ""),
            row["purchase_cost_with_vat"], row["purchase_vat_rate"],
            row["extra_cost_without_vat"], row["sale_vat_rate"],
        ) for row in prepared])
        conn.commit()
    return len(prepared)


def cost_template_products() -> list[dict]:
    """Return known Ozon SKUs with their latest cost, ready for CSV export."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            WITH product_sources AS (
                SELECT ozon_sku, product_name FROM finance_sku_daily
                UNION ALL SELECT ozon_sku, product_name FROM analytics_sku_daily
                UNION ALL SELECT ozon_sku, product_name FROM warehouse_stocks
            ), products AS (
                SELECT ozon_sku, MAX(product_name) AS product_name
                FROM product_sources GROUP BY ozon_sku
            )
            SELECT p.ozon_sku, p.product_name,
                   COALESCE(c.offer_id, '') AS offer_id,
                   COALESCE(c.product_group, '') AS product_group,
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
            COALESCE(m.delivered_units, 0) AS delivered_units,
            COALESCE(m.returned_units, 0) AS analytics_returned_units,
            COALESCE(m.canceled_units, 0) AS canceled_units,
            COALESCE(f.sales_amount, 0)::float AS sales_amount,
            COALESCE(f.return_amount, 0)::float AS return_amount,
            COALESCE(f.ozon_fees, 0)::float AS ozon_fees,
            COALESCE(f.net_payout, 0)::float AS net_payout,
            COALESCE(f.sales_units, 0) AS sales_units,
            COALESCE(f.return_units, 0) AS return_units,
            COALESCE(f.reward, 0)::float AS reward,
            COALESCE(f.delivery, 0)::float AS delivery,
            COALESCE(f.partner, 0)::float AS partner,
            COALESCE(f.fbo, 0)::float AS fbo,
            COALESCE(f.promotion, 0)::float AS promotion,
            COALESCE(f.other, 0)::float AS other,
            COALESCE(f.loan, 0)::float AS loan,
            COALESCE(f.factoring, 0)::float AS factoring,
            (f.day IS NOT NULL) AS finance_present
        FROM daily_metrics m
        FULL OUTER JOIN daily_finance f ON f.day = m.day
        WHERE COALESCE(m.day, f.day) BETWEEN %s AND %s
        ORDER BY day ASC
    """, (date_from, date_to))
    return cur.fetchall()


def _costs(cur, date_from: date, date_to: date) -> dict:
    cur.execute("""
        WITH analytics_available AS (
            SELECT EXISTS (
                SELECT 1 FROM analytics_sku_daily
                WHERE day BETWEEN %s AND %s AND (delivered_units > 0 OR returned_units > 0)
            ) AS present
        ), movements AS (
            SELECT day, ozon_sku, delivered_units AS sales_units,
                   returned_units AS return_units,
                   delivered_units - returned_units AS net_units
            FROM analytics_sku_daily, analytics_available
            WHERE day BETWEEN %s AND %s AND analytics_available.present
            UNION ALL
            SELECT day, ozon_sku, sales_units, return_units,
                   sales_units - return_units AS net_units
            FROM finance_sku_daily, analytics_available
            WHERE day BETWEEN %s AND %s AND NOT analytics_available.present
        ), priced AS (
            SELECT m.*,
                   c.purchase_cost_with_vat::float AS purchase_gross,
                   c.purchase_vat_rate::float AS purchase_vat_rate,
                   c.extra_cost_without_vat::float AS extra_cost_net,
                   c.sale_vat_rate::float AS sale_vat_rate
            FROM movements m
            LEFT JOIN LATERAL (
                SELECT * FROM sku_costs c
                WHERE c.ozon_sku = m.ozon_sku
                ORDER BY
                    CASE WHEN c.valid_from <= m.day THEN 0 ELSE 1 END,
                    CASE WHEN c.valid_from <= m.day THEN c.valid_from END DESC,
                    CASE WHEN c.valid_from > m.day THEN c.valid_from END ASC
                LIMIT 1
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
    """, (date_from, date_to, date_from, date_to, date_from, date_to))
    return cur.fetchone()


def _snapshot(cur, date_from: date, date_to: date) -> dict:
    rows = _period_rows(cur, date_from, date_to)
    costs = _costs(cur, date_from, date_to)
    totals = {
        "ordered_amount": sum(row["ordered_amount"] for row in rows),
        "ordered_units": sum(row["ordered_units"] for row in rows),
        "delivered_units": sum(row["delivered_units"] for row in rows),
        "analytics_returned_units": sum(row["analytics_returned_units"] for row in rows),
        "canceled_units": sum(row["canceled_units"] for row in rows),
        "sales_amount": sum(row["sales_amount"] for row in rows),
        "sales_units": sum(row["sales_units"] for row in rows),
        "return_amount": sum(row["return_amount"] for row in rows),
        "return_units": sum(row["return_units"] for row in rows),
        "ozon_fees": sum(row["ozon_fees"] for row in rows),
        "net_payout": sum(row["net_payout"] for row in rows),
        **{key: sum(row[key] for row in rows) for key in EXPENSE_KEYS},
        **{key: sum(row[key] for row in rows) for key in FINANCING_KEYS},
    }
    totals.update(costs)
    totals["rows"] = rows
    return totals


def _category_breakdown(cur, date_from: date, date_to: date) -> tuple[list[dict], int, float]:
    cur.execute("""
        WITH orders AS (
            SELECT ozon_sku, MAX(product_name) AS product_name,
                   SUM(ordered_amount)::float AS ordered_amount,
                   SUM(ordered_units)::int AS ordered_units,
                   SUM(delivered_units)::int AS delivered_units,
                   SUM(returned_units)::int AS returned_units
            FROM analytics_sku_daily WHERE day BETWEEN %s AND %s
            GROUP BY ozon_sku
        ), finance AS (
            SELECT f.ozon_sku, MAX(f.product_name) AS product_name,
                   SUM(f.sales_amount)::float AS sales_amount,
                   SUM(f.return_amount)::float AS return_amount,
                   SUM(f.ozon_fees)::float AS ozon_fees,
                   SUM(f.sales_units)::int AS sales_units,
                   SUM(f.return_units)::int AS return_units
            FROM finance_sku_daily f
            WHERE f.day BETWEEN %s AND %s
            GROUP BY f.ozon_sku
        ), analytics_available AS (
            SELECT EXISTS (
                SELECT 1 FROM orders WHERE delivered_units > 0 OR returned_units > 0
            ) AS present
        ), keys AS (
            SELECT ozon_sku FROM orders UNION SELECT ozon_sku FROM finance
        )
        SELECT k.ozon_sku,
               COALESCE(NULLIF(f.product_name, ''), o.product_name, '') AS product_name,
               COALESCE(o.ordered_amount, 0)::float AS ordered_amount,
               COALESCE(o.ordered_units, 0)::int AS ordered_units,
               COALESCE(f.sales_amount, 0)::float AS sales_amount,
               COALESCE(f.return_amount, 0)::float AS return_amount,
               COALESCE(f.ozon_fees, 0)::float AS ozon_fees,
               CASE WHEN available.present THEN COALESCE(o.delivered_units, 0) ELSE COALESCE(f.sales_units, 0) END::int AS sales_units,
               CASE WHEN available.present THEN COALESCE(o.returned_units, 0) ELSE COALESCE(f.return_units, 0) END::int AS return_units,
               c.product_group, c.sale_vat_rate::float AS sale_vat_rate,
               c.purchase_cost_with_vat::float AS purchase_gross,
               c.purchase_vat_rate::float AS purchase_vat_rate,
               c.extra_cost_without_vat::float AS extra_cost_net
        FROM keys k
        CROSS JOIN analytics_available available
        LEFT JOIN orders o ON o.ozon_sku = k.ozon_sku
        LEFT JOIN finance f ON f.ozon_sku = k.ozon_sku
        LEFT JOIN LATERAL (
            SELECT product_group, sale_vat_rate, purchase_cost_with_vat,
                   purchase_vat_rate, extra_cost_without_vat
            FROM sku_costs c
            WHERE c.ozon_sku = k.ozon_sku
            ORDER BY
                CASE WHEN c.valid_from <= %s THEN 0 ELSE 1 END,
                CASE WHEN c.valid_from <= %s THEN c.valid_from END DESC,
                CASE WHEN c.valid_from > %s THEN c.valid_from END ASC
            LIMIT 1
        ) c ON TRUE
    """, (date_from, date_to, date_from, date_to, date_to, date_to, date_to))
    groups = {
        key: {
            "key": key, "ordered_amount": 0.0, "ordered_units": 0,
            "sales_amount": 0.0, "return_amount": 0.0, "sales_units": 0,
            "return_units": 0, "ozon_fees": 0.0, "cogs_gross": 0.0,
            "cogs_net": 0.0, "movement_units": 0, "priced_units": 0,
            "output_vat": 0.0,
        }
        for key in ("men", "women", "kids", "unknown")
    }
    for row in cur.fetchall():
        key = product_group(row["product_name"], row.get("product_group") or "")
        target = groups[key]
        for field in (
            "ordered_amount", "ordered_units", "sales_amount", "return_amount",
            "sales_units", "return_units", "ozon_fees",
        ):
            target[field] += row[field]
        movement_units = abs(row["sales_units"]) + abs(row["return_units"])
        target["movement_units"] += movement_units
        if row["purchase_gross"] is not None:
            net_units = row["sales_units"] - row["return_units"]
            target["priced_units"] += movement_units
            target["cogs_gross"] += net_units * (row["purchase_gross"] + row["extra_cost_net"])
            target["cogs_net"] += net_units * (
                row["purchase_gross"] / (1 + row["purchase_vat_rate"] / 100) + row["extra_cost_net"]
            )
        rate = row["sale_vat_rate"] if row["sale_vat_rate"] is not None else (10.0 if key == "kids" else 22.0)
        target["output_vat"] += max(row["sales_amount"] - row["return_amount"], 0) * rate / (100 + rate)

    total_net_sales = sum(max(group["sales_amount"] - group["return_amount"], 0) for group in groups.values())
    # Some Ozon costs (advertising, storage, adjustments) are account-level and
    # have no SKU in the finance operation. Allocate that remainder by each
    # category's net-sales share so category profitability reconciles to the
    # shop total instead of silently omitting overhead.
    cur.execute("""
        SELECT COALESCE(SUM(ozon_fees), 0)::float AS total_ozon_fees
        FROM daily_finance WHERE day BETWEEN %s AND %s
    """, (date_from, date_to))
    total_ozon_fees = cur.fetchone()["total_ozon_fees"]
    attributed_fees = sum(group["ozon_fees"] for group in groups.values())
    if total_net_sales > 0 and attributed_fees < total_ozon_fees:
        unallocated = total_ozon_fees - attributed_fees
        for group in groups.values():
            group_sales = max(group["sales_amount"] - group["return_amount"], 0)
            group["ozon_fees"] += unallocated * group_sales / total_net_sales
    elif attributed_fees > 0 and attributed_fees > total_ozon_fees:
        scale = max(total_ozon_fees, 0) / attributed_fees
        for group in groups.values():
            group["ozon_fees"] *= scale
    labels = {"men": "Мужское", "women": "Женское", "kids": "Детское", "unknown": "Не распределено"}
    result = []
    for key, group in groups.items():
        if key == "unknown" and not (group["ordered_units"] or group["sales_units"]):
            continue
        net_sales = group["sales_amount"] - group["return_amount"]
        net_units = max(0, group["sales_units"] - group["return_units"])
        coverage = group["priced_units"] / group["movement_units"] * 100 if group["movement_units"] else 0.0
        costs_ready = group["movement_units"] > 0 and coverage >= 95.0
        costs_complete = coverage >= 99.9
        fees_net = group["ozon_fees"] - vat_part(group["ozon_fees"], settings.ozon_service_vat_rate)
        contribution_before_taxes = profit_before_tax = net_profit = None
        markup_before_tax = markup_after_tax = None
        if costs_ready:
            contribution_before_taxes = net_sales - group["cogs_gross"] - group["ozon_fees"]
            profit_before_tax = net_sales - group["output_vat"] - group["cogs_net"] - fees_net
            net_profit = profit_before_tax - max(profit_before_tax, 0) * settings.income_tax_rate / 100
            if group["cogs_gross"]:
                markup_before_tax = contribution_before_taxes / group["cogs_gross"] * 100
                markup_after_tax = net_profit / group["cogs_gross"] * 100
        result.append({
            "key": key, "name": labels[key],
            "ordered_amount": round(group["ordered_amount"], 2),
            "ordered_units": group["ordered_units"],
            "net_sales_amount": round(net_sales, 2),
            "net_sales_units": net_units,
            "sales_share_percent": round(max(net_sales, 0) / total_net_sales * 100, 1) if total_net_sales else 0.0,
            "buyout_rate": round(buyout_percent(group["sales_units"], group["return_units"]), 1) if group["sales_units"] else None,
            "ozon_fees": round(group["ozon_fees"], 2),
            "ozon_fees_share_percent": round(group["ozon_fees"] / net_sales * 100, 1) if net_sales > 0 else None,
            "cogs": round(group["cogs_gross"], 2) if costs_ready else None,
            "net_profit": round(net_profit, 2) if net_profit is not None else None,
            "markup_before_tax": round(markup_before_tax, 1) if markup_before_tax is not None else None,
            "markup_after_tax": round(markup_after_tax, 1) if markup_after_tax is not None else None,
            "cost_coverage_percent": round(coverage, 1),
            "status": (traffic_light(markup_after_tax, good=20, warning=5) if costs_complete else "yellow") if costs_ready else "neutral",
        })
    unknown_units = groups["unknown"]["ordered_units"] + groups["unknown"]["sales_units"]
    return result, unknown_units, sum(group["output_vat"] for group in groups.values())


def _warehouse_breakdown(cur) -> tuple[list[dict], str | None]:
    cur.execute("""
        SELECT w.warehouse_name,
               SUM(w.free_to_sell)::int AS units,
               SUM(w.reserved)::int AS reserved_units,
               SUM(w.promised)::int AS promised_units,
               SUM(w.free_to_sell * w.retail_price)::float AS retail_value,
               SUM(CASE WHEN c.purchase_cost_with_vat IS NOT NULL THEN
                   w.free_to_sell * (c.purchase_cost_with_vat + c.extra_cost_without_vat) ELSE 0 END)::float AS cost_value,
               SUM(CASE WHEN c.purchase_cost_with_vat IS NOT NULL THEN w.free_to_sell ELSE 0 END)::int AS priced_units,
               SUM(CASE WHEN w.retail_price > 0 THEN w.free_to_sell ELSE 0 END)::int AS retail_priced_units,
               MAX(w.updated_at) AS updated_at
        FROM warehouse_stocks w
        LEFT JOIN LATERAL (
            SELECT * FROM sku_costs c WHERE c.ozon_sku = w.ozon_sku
            ORDER BY c.valid_from DESC LIMIT 1
        ) c ON TRUE
        GROUP BY w.warehouse_name
        ORDER BY retail_value DESC, units DESC
    """)
    rows = cur.fetchall()
    total_value = sum(row["retail_value"] or 0 for row in rows)
    result = []
    latest = None
    for row in rows:
        units = max(int(row["units"] or 0), 0)
        priced_units = max(int(row["priced_units"] or 0), 0)
        retail_priced_units = max(int(row["retail_priced_units"] or 0), 0)
        cost_coverage = priced_units / units * 100 if units else 0.0
        retail_coverage = retail_priced_units / units * 100 if units else 0.0
        result.append({
            "warehouse_name": row["warehouse_name"], "units": units,
            "reserved_units": row["reserved_units"], "promised_units": row["promised_units"],
            "retail_value": round(row["retail_value"], 2) if retail_priced_units else None,
            "cost_value": round(row["cost_value"], 2) if priced_units else None,
            "share_percent": round((row["retail_value"] or 0) / total_value * 100, 1) if total_value else None,
            "cost_coverage_percent": round(cost_coverage, 1),
            "retail_coverage_percent": round(retail_coverage, 1),
            "missing_cost_units": max(units - priced_units, 0),
        })
        if row["updated_at"] and (latest is None or row["updated_at"] > latest):
            latest = row["updated_at"]
    return result, latest.isoformat() if latest else None


def _conversion(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(max(0.0, min(100.0, numerator / denominator * 100)), 1)


def photo_analytics(
    days: int = 30,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str = "",
) -> dict:
    """Return a SKU funnel tailored for the employee responsible for photos."""
    today = date.today()
    end = date_to or (today - timedelta(days=1))
    start = date_from or (end - timedelta(days=max(1, days) - 1))
    if start > end:
        raise ValueError("Дата начала не может быть позже даты окончания")
    needle = f"%{search.strip()}%"
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            WITH metrics AS (
                SELECT ozon_sku, MAX(product_name) AS product_name,
                       SUM(ordered_units)::int AS ordered_units,
                       SUM(delivered_units)::int AS delivered_units,
                       SUM(returned_units)::int AS returned_units,
                       SUM(hits_view_search)::int AS hits_view_search,
                       SUM(hits_view_pdp)::int AS hits_view_pdp,
                       SUM(hits_view)::int AS hits_view,
                       SUM(hits_tocart_search)::int AS hits_tocart_search,
                       SUM(hits_tocart_pdp)::int AS hits_tocart_pdp,
                       SUM(hits_tocart)::int AS hits_tocart,
                       SUM(session_view_search)::int AS session_view_search,
                       SUM(session_view_pdp)::int AS session_view_pdp
                FROM analytics_sku_daily
                WHERE day BETWEEN %s AND %s
                GROUP BY ozon_sku
            ), stock_meta AS (
                SELECT ozon_sku, MAX(offer_id) AS offer_id,
                       MAX(product_name) AS product_name
                FROM warehouse_stocks GROUP BY ozon_sku
            ), cost_meta AS (
                SELECT DISTINCT ON (ozon_sku) ozon_sku, offer_id, product_name
                FROM sku_costs ORDER BY ozon_sku, valid_from DESC
            ), finance AS (
                SELECT ozon_sku,
                       SUM(sales_units)::int AS sold_units,
                       SUM(return_units)::int AS returned_units
                FROM finance_sku_daily
                WHERE day BETWEEN %s AND %s
                GROUP BY ozon_sku
            )
            SELECT m.*,
                   COALESCE(NULLIF(p.offer_id, ''), NULLIF(c.offer_id, ''),
                            NULLIF(s.offer_id, ''), m.ozon_sku) AS offer_id,
                   COALESCE(NULLIF(p.product_name, ''), NULLIF(m.product_name, ''),
                            NULLIF(c.product_name, ''), s.product_name, '') AS display_name,
                   COALESCE(p.size, '') AS size,
                   COALESCE(p.barcode, '') AS barcode,
                   COALESCE(f.sold_units, 0) AS finance_sold_units,
                   COALESCE(f.returned_units, 0) AS finance_returned_units
            FROM metrics m
            LEFT JOIN product_catalog p ON p.ozon_sku = m.ozon_sku
            LEFT JOIN cost_meta c ON c.ozon_sku = m.ozon_sku
            LEFT JOIN stock_meta s ON s.ozon_sku = m.ozon_sku
            LEFT JOIN finance f ON f.ozon_sku = m.ozon_sku
            WHERE %s = '%%' OR CONCAT_WS(' ', m.ozon_sku, p.offer_id, c.offer_id,
                                         s.offer_id, p.product_name, m.product_name) ILIKE %s
            ORDER BY m.hits_view_pdp DESC, m.hits_view_search DESC, offer_id
            LIMIT 1000
        """, (start, end, start, end, needle, needle))
        source = cur.fetchall()
        cur.execute("""
            SELECT source, state, detail, last_attempt_at, last_success_at
            FROM sync_state WHERE source = 'photo_analytics'
        """)
        sync_row = cur.fetchone()

    traffic_available = any(
        row["hits_view_search"] or row["hits_view_pdp"] or row["hits_tocart"]
        for row in source
    )
    rows = []
    for row in source:
        finance_units_available = row["finance_sold_units"] > 0 or row["finance_returned_units"] > 0
        retained_units = (
            max(0, row["finance_sold_units"] - row["finance_returned_units"])
            if finance_units_available
            else max(0, row["delivered_units"] - row["returned_units"])
        )
        search_to_card = _conversion(row["hits_view_pdp"], row["hits_view_search"])
        rows.append({
            "ozon_sku": row["ozon_sku"],
            "offer_id": row["offer_id"],
            "product_name": row["display_name"],
            "size": row["size"],
            "barcode": row["barcode"],
            "views": row["hits_view_pdp"] if traffic_available else None,
            "search_catalog_impressions": row["hits_view_search"] if traffic_available else None,
            "ctr": search_to_card if traffic_available else None,
            "search_catalog_to_card": search_to_card if traffic_available else None,
            # Seller Analytics has no favorites metric. Keep the column explicit
            # instead of substituting a different event and misleading staff.
            "card_to_favorite": None,
            "card_to_cart": _conversion(row["hits_tocart_pdp"], row["hits_view_pdp"]) if traffic_available else None,
            "cart_to_order": _conversion(row["ordered_units"], row["hits_tocart"]) if traffic_available else None,
            "order_to_buyout": _conversion(retained_units, row["ordered_units"]),
            "cart_additions": row["hits_tocart"] if traffic_available else None,
            "card_cart_additions": row["hits_tocart_pdp"] if traffic_available else None,
            "ordered_units": row["ordered_units"],
            "retained_units": retained_units,
        })

    totals = {
        "views": sum(row["views"] or 0 for row in rows) if traffic_available else None,
        "search_catalog_impressions": sum(row["search_catalog_impressions"] or 0 for row in rows) if traffic_available else None,
        "cart_additions": sum(row["cart_additions"] or 0 for row in rows) if traffic_available else None,
        "card_cart_additions": sum(row["card_cart_additions"] or 0 for row in rows) if traffic_available else None,
        "ordered_units": sum(row["ordered_units"] for row in rows),
        "retained_units": sum(row["retained_units"] for row in rows),
    }
    totals.update({
        "ctr": _conversion(totals["views"], totals["search_catalog_impressions"]) if traffic_available else None,
        "card_to_cart": _conversion(totals["card_cart_additions"], totals["views"]) if traffic_available else None,
        "cart_to_order": _conversion(totals["ordered_units"], totals["cart_additions"]) if traffic_available else None,
        "order_to_buyout": _conversion(totals["retained_units"], totals["ordered_units"]),
    })
    return {
        "period": {
            "date_from": start.isoformat(), "date_to": end.isoformat(),
            "days": (end - start).days + 1,
        },
        "search": search.strip(),
        "rows": rows,
        "totals": totals,
        "favorites_available": False,
        "data_available": traffic_available,
        "buyout_available": totals["retained_units"] > 0,
        "source_status": {
            "state": sync_row["state"] if sync_row else "waiting",
            "detail": sync_row["detail"] if sync_row else "Ожидаем первую синхронизацию Ozon Analytics.",
            "last_attempt_at": sync_row["last_attempt_at"].isoformat() if sync_row and sync_row["last_attempt_at"] else None,
            "last_success_at": sync_row["last_success_at"].isoformat() if sync_row and sync_row["last_success_at"] else None,
        },
    }


def _delta_note(current: float, previous: float, fallback: str) -> str:
    change = percent_change(current, previous)
    if change is None:
        return fallback
    arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
    return f"{arrow} {abs(change):g}% к прошлому периоду"


def _card(
    key: str, name: str, value: float | None, unit: str, status: str,
    note: str, tooltip: str, *, percent: float | None = None,
    percent_label: str = "",
) -> dict:
    return {
        "key": key, "name": name,
        "value": None if value is None else round(value, 2), "unit": unit,
        "status": status, "note": note, "tooltip": tooltip,
        "percent": None if percent is None else round(percent, 1),
        "percent_label": percent_label,
    }


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
        categories, unclassified_units, sku_output_vat = _category_breakdown(cur, start, end)
        warehouses, inventory_updated_at = _warehouse_breakdown(cur)

    movement_units = current["movement_units"]
    coverage = round(current["priced_units"] / movement_units * 100, 1) if movement_units else 0.0
    costs_ready = movement_units > 0 and coverage >= 95.0
    costs_complete = coverage >= 99.9
    finance_ready = any(row["finance_present"] for row in current["rows"])
    analytics_units_ready = current["delivered_units"] > 0

    net_sales_gross = current["sales_amount"] - current["return_amount"]
    previous_net_sales = previous["sales_amount"] - previous["return_amount"]
    blended_vat_rate = current["weighted_vat"] / current["weighted_units"] if current["weighted_units"] else settings.default_sale_vat_rate
    vat_fraction = current["weighted_vat_fraction"] / current["weighted_units"] if current["weighted_units"] else settings.default_sale_vat_rate / (100 + settings.default_sale_vat_rate)
    output_vat = sku_output_vat if sku_output_vat > 0 else max(net_sales_gross, 0) * vat_fraction
    ozon_input_vat = vat_part(current["ozon_fees"], settings.ozon_service_vat_rate)
    ozon_fees_net = current["ozon_fees"] - ozon_input_vat
    vat_payable = max(output_vat - current["input_vat"] - ozon_input_vat, 0)
    contribution_before_taxes = profit_before_tax = income_tax = net_profit = None
    markup_before_tax = markup_after_tax = net_margin = None
    if costs_ready and finance_ready:
        contribution_before_taxes = net_sales_gross - current["cogs_gross"] - current["ozon_fees"]
        profit_before_tax = net_sales_gross - output_vat - current["cogs_net"] - ozon_fees_net
        income_tax = max(profit_before_tax, 0) * settings.income_tax_rate / 100
        net_profit = profit_before_tax - income_tax
        if current["cogs_gross"]:
            markup_before_tax = contribution_before_taxes / current["cogs_gross"] * 100
            markup_after_tax = net_profit / current["cogs_gross"] * 100
        sales_net_of_vat = net_sales_gross - output_vat
        if sales_net_of_vat:
            net_margin = net_profit / sales_net_of_vat * 100

    gross_sold_units = current["delivered_units"] if analytics_units_ready else current["sales_units"]
    returned_units = current["analytics_returned_units"] if analytics_units_ready else current["return_units"]
    previous_gross_sold_units = previous["delivered_units"] if previous["delivered_units"] > 0 else previous["sales_units"]
    previous_returned_units = previous["analytics_returned_units"] if previous["delivered_units"] > 0 else previous["return_units"]
    net_sold_units = max(0, gross_sold_units - returned_units)
    buyout_rate = buyout_percent(gross_sold_units, returned_units)
    return_rate = returned_units / gross_sold_units * 100 if gross_sold_units else 0.0
    cogs_share = current["cogs_gross"] / net_sales_gross * 100 if net_sales_gross > 0 and costs_ready else None
    return_amount_share = current["return_amount"] / current["sales_amount"] * 100 if current["sales_amount"] else 0.0
    ozon_fees_share = current["ozon_fees"] / net_sales_gross * 100 if net_sales_gross > 0 and finance_ready else None

    period_note = f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"
    missing_cost_units = max(movement_units - current["priced_units"], 0)
    costs_note = (
        f"Покрытие {coverage:g}% · без цены {missing_cost_units} движений"
        if movement_units else "Загрузите себестоимость по SKU"
    )
    profit_note = (
        f"{'Предварительно · ' if not costs_complete else ''}рентабельность {net_margin:.1f}%"
        if net_margin is not None else costs_note
    )

    return_amount_status = "red" if previous["return_amount"] == 0 and current["return_amount"] > 0 else trend_status(current["return_amount"], previous["return_amount"], inverse=True)
    return_units_status = "red" if previous_returned_units == 0 and returned_units > 0 else trend_status(returned_units, previous_returned_units, inverse=True)
    kpis = [
        _card("ordered_amount", "Заказано на сумму", current["ordered_amount"], "₽", trend_status(current["ordered_amount"], previous["ordered_amount"]), _delta_note(current["ordered_amount"], previous["ordered_amount"], period_note), "Стоимость всех оформленных заказов за выбранный период.", percent=percent_change(current["ordered_amount"], previous["ordered_amount"]), percent_label="к прошлому периоду"),
        _card("ordered_units", "Заказано товаров", current["ordered_units"], "шт.", trend_status(current["ordered_units"], previous["ordered_units"]), _delta_note(current["ordered_units"], previous["ordered_units"], period_note), "Количество заказанных единиц, включая те, что позже могли отменить или вернуть."),
        _card("sales_amount", "Продажи", current["sales_amount"] if finance_ready else None, "₽", trend_status(current["sales_amount"], previous["sales_amount"]) if finance_ready else "neutral", _delta_note(current["sales_amount"], previous["sales_amount"], "Финансовые операции Ozon"), "Начисления за фактически реализованные товары до вычета возвратов.", percent=percent_change(current["sales_amount"], previous["sales_amount"]), percent_label="к прошлому периоду"),
        _card("sales_units", "Продано товаров", gross_sold_units if analytics_units_ready or finance_ready else None, "шт.", trend_status(gross_sold_units, previous_gross_sold_units) if analytics_units_ready or finance_ready else "neutral", _delta_note(gross_sold_units, previous_gross_sold_units, "По данным Ozon Analytics"), "Количество доставленных покупателям товаров до последующих возвратов."),
        _card("return_amount", "Возвраты", current["return_amount"] if finance_ready else None, "₽", return_amount_status if finance_ready else "neutral", _delta_note(current["return_amount"], previous["return_amount"], f"Доля возвратов {return_rate:.1f}%"), "Сумма возвратов и сторнированных начислений. Чем меньше, тем лучше.", percent=return_amount_share if finance_ready else None, percent_label="от продаж"),
        _card("return_units", "Возвращено товаров", returned_units if analytics_units_ready or finance_ready else None, "шт.", return_units_status if analytics_units_ready or finance_ready else "neutral", _delta_note(returned_units, previous_returned_units, f"Доля возвратов {return_rate:.1f}%"), "Количество возвращённых единиц из единого отчёта Ozon Analytics."),
        _card("ozon_fees", "Расходы Ozon", current["ozon_fees"] if finance_ready else None, "₽", "neutral" if not finance_ready else traffic_light(ozon_fees_share, good=25, warning=40, inverse=True), "Комиссии, логистика и услуги", "Комиссии, логистика, эквайринг и услуги Ozon. Займы и факторинг сюда не входят.", percent=ozon_fees_share, percent_label="от продаж после возвратов"),
        _card("cogs", "Себестоимость продаж", current["cogs_gross"] if costs_ready else None, "₽", (traffic_light(cogs_share, good=50, warning=70, inverse=True) if costs_complete else "yellow") if costs_ready else "neutral", costs_note, "Закупочная стоимость с НДС плюс дополнительные затраты без НДС, с учётом возвратов.", percent=cogs_share, percent_label="от продаж после возвратов"),
        _card("net_profit", "Чистая прибыль", net_profit, "₽", (traffic_light(net_margin, good=15, warning=5) if costs_complete else "yellow") if net_profit is not None else "neutral", profit_note, "Расчётная прибыль после расходов Ozon, НДС и налога на прибыль. При неполной себестоимости помечается как предварительная.", percent=net_margin, percent_label="чистая рентабельность"),
        _card("buyout_rate", "Процент выкупа", buyout_rate, "%", traffic_light(buyout_rate, good=80, warning=60), f"Осталось у покупателей: {net_sold_units} из {gross_sold_units} доставленных", "Доставленные товары за вычетом возвратов относительно всех доставленных единиц."),
        _card("markup_before_tax", "Наценка до налогов", markup_before_tax, "%", (traffic_light(markup_before_tax, good=30, warning=10) if costs_complete else "yellow") if markup_before_tax is not None else "neutral", "После себестоимости и всех расходов Ozon", "Продажи после возвратов минус себестоимость с НДС и все расходы Ozon, делённые на себестоимость с НДС."),
        _card("markup_after_tax", "Наценка после налогов", markup_after_tax, "%", (traffic_light(markup_after_tax, good=20, warning=5) if costs_complete else "yellow") if markup_after_tax is not None else "neutral", f"НДС ≈ {blended_vat_rate:.1f}%, налог на прибыль {settings.income_tax_rate:g}%", "Чистая прибыль после расчётных НДС и налога на прибыль, делённая на себестоимость с НДС."),
    ]

    insights = []
    if not finance_ready:
        insights.append({"status": "yellow", "title": "Финансовые данные загружаются", "text": "Продажи, возвраты и расходы Ozon появятся после финансовой синхронизации."})
    if not costs_ready:
        insights.append({"status": "yellow", "title": "Нужна себестоимость", "text": "Загрузите CSV по Ozon SKU, чтобы рассчитать прибыль и наценку."})
    elif not costs_complete:
        insights.append({"status": "yellow", "title": "Прибыль предварительная", "text": f"Не хватает себестоимости для {missing_cost_units} движений. Остальные данные уже включены в расчёт."})
    if unclassified_units:
        insights.append({"status": "yellow", "title": "Есть товары без категории", "text": f"Не распределено движений: {unclassified_units}. Укажите категорию в CSV себестоимости."})
    if not warehouses:
        insights.append({"status": "yellow", "title": "Остатки FBO ещё не загружены", "text": "Данные по складам появятся после следующей синхронизации Seller API."})
    if buyout_rate is not None and buyout_rate < 60:
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
            "input_vat_cogs": round(current["input_vat"], 2) if costs_ready else None,
            "input_vat_ozon": round(ozon_input_vat, 2),
            "vat_payable": round(vat_payable, 2) if costs_ready else None,
            "ozon_fees": round(current["ozon_fees"], 2),
            "cogs_net": round(current["cogs_net"], 2) if costs_ready else None,
            "profit_before_tax": round(profit_before_tax, 2) if profit_before_tax is not None else None,
            "income_tax": round(income_tax, 2) if income_tax is not None else None,
            "net_profit": round(net_profit, 2) if net_profit is not None else None,
        },
        "ozon_expenses": [
            {
                "key": key,
                "name": {
                    "reward": "Вознаграждение Ozon",
                    "delivery": "Услуги доставки и логистика",
                    "partner": "Услуги партнёров и эквайринг",
                    "fbo": "Услуги FBO и хранение",
                    "promotion": "Продвижение и реклама",
                    "other": "Другие услуги и штрафы",
                }[key],
                "value": round(current[key], 2),
                "share_percent": round(current[key] / net_sales_gross * 100, 1) if net_sales_gross > 0 else None,
            }
            for key in EXPENSE_KEYS
        ],
        "financing": [
            {
                "key": key,
                "name": {"loan": "Займы Ozon", "factoring": "Факторинг"}[key],
                "value": round(current[key], 2),
                "direction": "inflow" if current[key] > 0 else "outflow" if current[key] < 0 else "none",
            }
            for key in FINANCING_KEYS
        ],
        "tax_policy": {"adult_vat_rate": 22, "kids_vat_rate": 10, "income_tax_rate": settings.income_tax_rate},
        "categories": categories,
        "warehouses": warehouses,
        "data_quality": {
            "finance_ready": finance_ready,
            "units_source": "analytics" if analytics_units_ready else "finance_fallback",
            "cost_coverage_percent": coverage,
            "missing_cost_units": missing_cost_units,
            "tax_estimate": True,
            "earliest_date": availability["earliest"].isoformat() if availability and availability["earliest"] else None,
            "latest_date": availability["latest"].isoformat() if availability and availability["latest"] else None,
            "last_sync": availability["last_sync"].isoformat() if availability and availability["last_sync"] else None,
            "inventory_updated_at": inventory_updated_at,
            "unclassified_units": unclassified_units,
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
