import logging
import re
from datetime import date, timedelta

from app.clients.ozon_seller import OzonSellerClient
from app.config import settings
from app.services.finance import aggregate_finance_operations
from app.services.storage import finance_has_data, replace_finance_period, upsert_daily_metric

logger = logging.getLogger(__name__)


def metric_number(value: object) -> float:
    """Analytics API versions return either numbers or objects with a value."""
    if isinstance(value, dict):
        value = value.get("value", value.get("id", 0))
    return float(value or 0)


def report_day(dimension: object) -> date:
    if isinstance(dimension, dict):
        dimension = dimension.get("id") or dimension.get("name") or ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(dimension))
    if not match:
        raise ValueError("Analytics response does not contain an ISO day")
    return date.fromisoformat(match.group(0))


async def sync_operational_data() -> None:
    """Collect analytics and finance independently so one failure cannot hide the other."""
    if not settings.sync_enabled or not settings.ozon_api_key or not settings.ozon_client_id:
        logger.info("Ozon sync skipped: configure new credentials and set SYNC_ENABLED=true")
        return
    today = date.today()
    client = OzonSellerClient()
    try:
        # Seller Analytics allows the latest three months without Premium Plus.
        # Keeping that window locally also makes arbitrary date filters useful.
        payload = await client.daily_analytics(today - timedelta(days=90), today - timedelta(days=1))
    except Exception as error:
        # Credentials and response contents are intentionally omitted from logs.
        logger.warning("Ozon analytics sync deferred: %s", type(error).__name__)
    else:
        for item in payload.get("result", {}).get("data", []):
            dimensions = item.get("dimensions", [])
            metrics = item.get("metrics", [])
            if not dimensions or not metrics:
                continue
            try:
                day = report_day(dimensions[0])
                revenue = metric_number(metrics[0]) if len(metrics) > 0 else 0
                ordered_units = int(metric_number(metrics[1])) if len(metrics) > 1 else 0
                cancellations = int(metric_number(metrics[2])) if len(metrics) > 2 else 0
                upsert_daily_metric(day, revenue, ordered_units, cancellations)
            except (TypeError, ValueError, IndexError):
                logger.warning("Skipped analytics row with unexpected Ozon format")
        logger.info("Ozon analytics sync completed")

    finance_days = 92 if not finance_has_data() else 14
    finance_from = today - timedelta(days=finance_days)
    finance_to = today - timedelta(days=1)
    try:
        await sync_finance_data(client, finance_from, finance_to)
    except Exception as error:
        logger.warning("Ozon finance sync deferred: %s", type(error).__name__)


async def sync_finance_data(client: OzonSellerClient, date_from: date, date_to: date) -> None:
    """Fetch finance operations in 31-day chunks and replace stored aggregates."""
    chunk_from = date_from
    while chunk_from <= date_to:
        chunk_to = min(chunk_from + timedelta(days=30), date_to)
        page = 1
        operations: list[dict] = []
        while True:
            payload = await client.finance_transactions(chunk_from, chunk_to, page=page)
            result = payload.get("result") or {}
            operations.extend(result.get("operations") or [])
            page_count = int(result.get("page_count") or 1)
            if page >= page_count:
                break
            page += 1
        daily, sku_daily = aggregate_finance_operations(operations)
        replace_finance_period(chunk_from, chunk_to, daily, sku_daily)
        chunk_from = chunk_to + timedelta(days=1)
    logger.info("Ozon finance sync completed")
