"""Small PostgreSQL storage layer for Ozon aggregates.

Raw orders are intentionally not retained in the first release; this keeps the
dashboard fast and limits stored customer information.
"""
from contextlib import contextmanager
from datetime import date, datetime

import psycopg
from psycopg.rows import dict_row

from app.config import settings


@contextmanager
def connection():
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def initialise() -> None:
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
            CREATE TABLE IF NOT EXISTS sync_runs (
                id BIGSERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ,
                state TEXT NOT NULL,
                detail TEXT
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


def dashboard(days: int = 30) -> dict:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT day, revenue::float AS revenue, ordered_units, canceled_units
            FROM daily_metrics
            WHERE day >= current_date - %s
            ORDER BY day ASC
        """, (days - 1,))
        rows = cur.fetchall()
    revenue = sum(float(row["revenue"]) for row in rows)
    ordered = sum(int(row["ordered_units"]) for row in rows)
    canceled = sum(int(row["canceled_units"]) for row in rows)
    cancellation_rate = round((canceled / ordered * 100), 2) if ordered else 0
    return {
        "status": "ready" if rows else "waiting_for_sync",
        "period_days": days,
        "kpis": [
            {"name": "Выручка", "value": round(revenue, 2), "unit": "₽", "status": "green" if revenue else "neutral", "note": f"За {days} дней"},
            {"name": "Заказы", "value": ordered, "unit": "шт.", "status": "green" if ordered else "neutral", "note": f"За {days} дней"},
            {"name": "Отмены", "value": cancellation_rate, "unit": "%", "status": "red" if cancellation_rate >= 10 else "yellow" if cancellation_rate >= 5 else "green", "note": f"{canceled} единиц"},
        ],
        "series": [{"day": row["day"].isoformat(), "revenue": float(row["revenue"]), "ordered_units": row["ordered_units"]} for row in rows],
        "insights": [],
    }
