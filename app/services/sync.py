import asyncio
import logging
import re
from datetime import date, timedelta

import httpx

from app.clients.ozon_seller import OzonSellerClient
from app.config import settings
from app.services.finance import aggregate_finance_operations
from app.services.storage import finance_earliest_day, replace_finance_period, set_sync_state, upsert_daily_metric

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
    results = await asyncio.gather(
        sync_analytics_source(OzonSellerClient(), today),
        sync_finance_source(OzonSellerClient(), today),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.error("Ozon background sync failed outside a data source handler: %s", type(result).__name__)


def sync_error_detail(error: Exception) -> str:
    """Translate an API failure into a safe message that can be shown in the dashboard."""
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code == 429:
            return "Ozon ограничил частоту запросов. Повторим синхронизацию автоматически."
        if code in {401, 403}:
            return "Нет доступа к разделу API. Проверьте права ключа Seller API в кабинете Ozon."
        return f"Ozon API временно вернул ошибку {code}."
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return "Ozon API временно недоступен. Повторим синхронизацию автоматически."
    return "Синхронизация временно не завершилась. Повторим автоматически."


async def sync_analytics_source(client: OzonSellerClient, today: date) -> None:
    set_sync_state("analytics", "syncing", "Загружаем заказы за последние 90 дней")
    try:
        # Seller Analytics allows the latest three months without Premium Plus.
        # Keeping that window locally also makes arbitrary date filters useful.
        payload = await client.daily_analytics(today - timedelta(days=90), today - timedelta(days=1))
    except Exception as error:
        # Credentials and response contents are intentionally omitted from logs.
        detail = sync_error_detail(error)
        set_sync_state("analytics", "error", detail)
        logger.warning("Ozon analytics sync deferred: %s", type(error).__name__)
        return

    imported = 0
    skipped = 0
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
            imported += 1
        except (TypeError, ValueError, IndexError):
            skipped += 1
    set_sync_state("analytics", "ready", f"Заказы обновлены: {imported} дней", success=True)
    if skipped:
        logger.warning("Skipped %s analytics rows with unexpected Ozon format", skipped)
    logger.info("Ozon analytics sync completed")


async def sync_finance_source(client: OzonSellerClient, today: date) -> None:
    desired_from = today - timedelta(days=92)
    earliest = finance_earliest_day()
    finance_from = desired_from if earliest is None or earliest > desired_from else today - timedelta(days=14)
    finance_to = today - timedelta(days=1)
    set_sync_state("finance", "syncing", "Загружаем продажи, возвраты и расходы Ozon")
    try:
        operation_count = await sync_finance_data(client, finance_from, finance_to)
    except Exception as error:
        set_sync_state("finance", "error", sync_error_detail(error))
        logger.warning("Ozon finance sync deferred: %s", type(error).__name__)
        return
    set_sync_state("finance", "ready", f"Финансовые операции обновлены: {operation_count}", success=True)


async def sync_finance_data(client: OzonSellerClient, date_from: date, date_to: date) -> int:
    """Fetch newest finance operations first, then backfill earlier 31-day chunks."""
    operation_count = 0
    for chunk_from, chunk_to in finance_chunks(date_from, date_to):
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
            # Large stores can have many finance pages. A small pause prevents
            # avoidable bursts and lets the newest period finish reliably.
            await asyncio.sleep(1.0)
        daily, sku_daily = aggregate_finance_operations(operations)
        replace_finance_period(chunk_from, chunk_to, daily, sku_daily)
        operation_count += len(operations)
    logger.info("Ozon finance sync completed")
    return operation_count


def finance_chunks(date_from: date, date_to: date) -> list[tuple[date, date]]:
    """Split a period into newest-first API windows of at most 31 days."""
    chunks: list[tuple[date, date]] = []
    chunk_to = date_to
    while chunk_to >= date_from:
        chunk_from = max(date_from, chunk_to - timedelta(days=30))
        chunks.append((chunk_from, chunk_to))
        chunk_to = chunk_from - timedelta(days=1)
    return chunks
