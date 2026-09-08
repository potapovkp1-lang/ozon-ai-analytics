import asyncio
import logging
import re
from datetime import date, timedelta

import httpx

from app.clients.ozon_seller import OzonSellerClient
from app.config import settings
from app.services.finance import aggregate_finance_operations
from app.services.storage import (
    finance_earliest_day,
    finance_needs_sku_backfill,
    replace_analytics_sku_period,
    replace_analytics_sku_snapshot,
    replace_finance_period,
    replace_inventory_snapshot,
    replace_product_catalog,
    set_sync_state,
    upsert_daily_metric,
)

logger = logging.getLogger(__name__)
_sync_lock = asyncio.Lock()


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


def dimension_value(dimension: object) -> tuple[str, str]:
    if isinstance(dimension, dict):
        return str(dimension.get("id") or ""), str(dimension.get("name") or "")
    return str(dimension or ""), ""


def product_catalog_row(item: dict) -> dict | None:
    """Extract stable staff-facing metadata from a product attribute row."""
    sku = str(item.get("sku") or "")
    if not sku:
        for source in item.get("sources") or []:
            if source.get("sku"):
                sku = str(source["sku"])
                break
    if not sku:
        return None
    raw_barcodes = item.get("barcodes") or item.get("barcode") or []
    if isinstance(raw_barcodes, str):
        barcode = raw_barcodes
    else:
        barcode = str(next((value for value in raw_barcodes if value), ""))
    size = ""
    candidates: list[tuple[int, str]] = []
    for attribute in item.get("attributes") or []:
        name = str(attribute.get("name") or "").strip().lower()
        if "размер" not in name or any(token in name for token in ("упаков", "изображ", "габарит")):
            continue
        values = [str(value.get("value") or value.get("dictionary_value_id") or "").strip()
                  for value in attribute.get("values") or []]
        value = ", ".join(value for value in values if value)
        if value:
            candidates.append((0 if name == "размер" else 1, value))
    if candidates:
        size = sorted(candidates)[0][1]
    return {
        "ozon_sku": sku,
        "offer_id": str(item.get("offer_id") or ""),
        "product_name": str(item.get("name") or ""),
        "size": size,
        "barcode": barcode,
    }


async def sync_operational_data() -> None:
    """Collect analytics and finance independently so one failure cannot hide the other."""
    if not settings.sync_enabled or not settings.ozon_api_key or not settings.ozon_client_id:
        logger.info("Ozon sync skipped: configure new credentials and set SYNC_ENABLED=true")
        return
    if _sync_lock.locked():
        logger.info("Ozon sync skipped: the previous refresh is still running")
        return
    async with _sync_lock:
        today = date.today()
        results = await asyncio.gather(
            sync_analytics_source(OzonSellerClient(), today),
            sync_finance_source(OzonSellerClient(), today),
            sync_inventory_source(OzonSellerClient()),
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


def analytics_chunks(date_from: date, date_to: date, days: int = 7) -> list[tuple[date, date]]:
    """Return newest-first windows so recent dashboard data becomes visible first."""
    chunks: list[tuple[date, date]] = []
    end = date_to
    while end >= date_from:
        start = max(date_from, end - timedelta(days=days - 1))
        chunks.append((start, end))
        end = start - timedelta(days=1)
    return chunks


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
            delivered_units = int(metric_number(metrics[2])) if len(metrics) > 2 else 0
            returns = int(metric_number(metrics[3])) if len(metrics) > 3 else 0
            cancellations = int(metric_number(metrics[4])) if len(metrics) > 4 else 0
            upsert_daily_metric(day, revenue, ordered_units, delivered_units, returns, cancellations)
            imported += 1
        except (TypeError, ValueError, IndexError):
            skipped += 1
    sku_imported = 0
    snapshot_imported = 0
    for snapshot_days in (30, 7):
        try:
            # Ozon allows this method once per minute. A compact SKU snapshot
            # fills the visible 30/7-day table before the longer daily backfill.
            await asyncio.sleep(70)
            snapshot_imported += await sync_sku_snapshot(
                client,
                today - timedelta(days=snapshot_days),
                today - timedelta(days=1),
            )
        except Exception as error:
            set_sync_state("photo_analytics", "error", sync_error_detail(error))
            logger.warning("Ozon SKU snapshot sync deferred: %s", type(error).__name__)
            break
    try:
        await asyncio.sleep(70)
        sku_imported = await sync_sku_analytics(client, today - timedelta(days=90), today - timedelta(days=1))
    except Exception as error:
        if snapshot_imported == 0:
            set_sync_state("photo_analytics", "error", sync_error_detail(error))
        logger.warning("Ozon daily SKU analytics sync deferred: %s", type(error).__name__)
    set_sync_state("analytics", "ready", f"Заказы: {imported} дней, детализация: {sku_imported + snapshot_imported} строк", success=True)
    if skipped:
        logger.warning("Skipped %s analytics rows with unexpected Ozon format", skipped)
    logger.info("Ozon analytics sync completed")


async def sync_sku_analytics(client: OzonSellerClient, date_from: date, date_to: date) -> int:
    imported = 0
    request_made = False
    recent_traffic = 0
    recent_orders = 0
    set_sync_state("photo_analytics", "syncing", "Сначала загружаем свежие 7 дней")
    for chunk_index, (chunk_from, chunk_to) in enumerate(analytics_chunks(date_from, date_to)):
        offset = 0
        rows: list[dict] = []
        while True:
            if request_made:
                # The endpoint allows one request per minute, including pagination.
                await asyncio.sleep(70)
            payload = await client.sku_analytics(chunk_from, chunk_to, offset=offset)
            request_made = True
            data = payload.get("result", {}).get("data", [])
            for item in data:
                dimensions = item.get("dimensions") or []
                metrics = item.get("metrics") or []
                if len(dimensions) < 2:
                    continue
                try:
                    day = report_day(dimensions[0])
                    sku, name = dimension_value(dimensions[1])
                    if not sku:
                        continue
                    rows.append({
                        "day": day,
                        "ozon_sku": sku,
                        "product_name": name,
                        "ordered_amount": metric_number(metrics[0]) if len(metrics) > 0 else 0,
                        "ordered_units": int(metric_number(metrics[1])) if len(metrics) > 1 else 0,
                        "delivered_units": int(metric_number(metrics[2])) if len(metrics) > 2 else 0,
                        "returned_units": int(metric_number(metrics[3])) if len(metrics) > 3 else 0,
                        "canceled_units": int(metric_number(metrics[4])) if len(metrics) > 4 else 0,
                        "hits_view_search": int(metric_number(metrics[5])) if len(metrics) > 5 else 0,
                        "hits_view_pdp": int(metric_number(metrics[6])) if len(metrics) > 6 else 0,
                        "hits_view": int(metric_number(metrics[7])) if len(metrics) > 7 else 0,
                        "hits_tocart_search": int(metric_number(metrics[8])) if len(metrics) > 8 else 0,
                        "hits_tocart_pdp": int(metric_number(metrics[9])) if len(metrics) > 9 else 0,
                        "hits_tocart": int(metric_number(metrics[10])) if len(metrics) > 10 else 0,
                        "session_view_search": int(metric_number(metrics[11])) if len(metrics) > 11 else 0,
                        "session_view_pdp": int(metric_number(metrics[12])) if len(metrics) > 12 else 0,
                        "conv_tocart_pdp": metric_number(metrics[13]) if len(metrics) > 13 else None,
                    })
                except (TypeError, ValueError, IndexError):
                    continue
            if len(data) < 1000:
                break
            offset += 1000
        replace_analytics_sku_period(chunk_from, chunk_to, rows)
        imported += len(rows)
        if chunk_index == 0:
            recent_orders = sum(row["ordered_units"] for row in rows)
            recent_traffic = sum(row["hits_view_search"] + row["hits_view_pdp"] for row in rows)
            if recent_orders > 0 and recent_traffic == 0:
                set_sync_state(
                    "photo_analytics", "partial",
                    "Заказы обновлены, расширенные метрики Premium Plus ещё догружаются.",
                )
            else:
                set_sync_state(
                    "photo_analytics", "ready",
                    f"Свежие 7 дней обновлены: {len(rows)} строк",
                    success=True,
                )
    if recent_orders == 0 or recent_traffic > 0:
        set_sync_state("photo_analytics", "ready", f"Воронка обновлена: {imported} строк", success=True)
    return imported


async def sync_sku_snapshot(client: OzonSellerClient, date_from: date, date_to: date) -> int:
    """Fetch an exact-period Premium Plus SKU funnel without the costly day dimension."""
    rows: list[dict] = []
    offset = 0
    while True:
        payload = await client.sku_analytics_snapshot(date_from, date_to, offset=offset)
        data = payload.get("result", {}).get("data", [])
        for item in data:
            dimensions = item.get("dimensions") or []
            metrics = item.get("metrics") or []
            if not dimensions:
                continue
            try:
                sku, name = dimension_value(dimensions[0])
                if not sku:
                    continue
                rows.append({
                    "ozon_sku": sku,
                    "product_name": name,
                    "ordered_amount": metric_number(metrics[0]) if len(metrics) > 0 else 0,
                    "ordered_units": int(metric_number(metrics[1])) if len(metrics) > 1 else 0,
                    "delivered_units": int(metric_number(metrics[2])) if len(metrics) > 2 else 0,
                    "returned_units": int(metric_number(metrics[3])) if len(metrics) > 3 else 0,
                    "canceled_units": int(metric_number(metrics[4])) if len(metrics) > 4 else 0,
                    "hits_view_search": int(metric_number(metrics[5])) if len(metrics) > 5 else 0,
                    "hits_view_pdp": int(metric_number(metrics[6])) if len(metrics) > 6 else 0,
                    "hits_view": int(metric_number(metrics[7])) if len(metrics) > 7 else 0,
                    "hits_tocart_search": int(metric_number(metrics[8])) if len(metrics) > 8 else 0,
                    "hits_tocart_pdp": int(metric_number(metrics[9])) if len(metrics) > 9 else 0,
                    "hits_tocart": int(metric_number(metrics[10])) if len(metrics) > 10 else 0,
                    "session_view_search": int(metric_number(metrics[11])) if len(metrics) > 11 else 0,
                    "session_view_pdp": int(metric_number(metrics[12])) if len(metrics) > 12 else 0,
                    "conv_tocart_pdp": metric_number(metrics[13]) if len(metrics) > 13 else None,
                })
            except (TypeError, ValueError, IndexError):
                continue
        if len(data) < 1000:
            break
        offset += 1000
        await asyncio.sleep(70)
    replace_analytics_sku_snapshot(date_from, date_to, rows)
    traffic = sum(row["hits_view_search"] + row["hits_view_pdp"] for row in rows)
    state = "ready" if traffic > 0 else "partial"
    detail = (
        f"Premium Plus: обновлено {len(rows)} товаров за {(date_to - date_from).days + 1} дней"
        if traffic > 0
        else "Ozon принял запрос Premium Plus, но пока вернул пустые показатели трафика."
    )
    set_sync_state("photo_analytics", state, detail, success=traffic > 0)
    return len(rows)


def current_price(item: dict) -> float:
    price = item.get("price") or {}
    for key in ("marketing_seller_price", "marketing_price", "price", "retail_price"):
        try:
            value = float(price.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0.0


async def sync_product_catalog(client: OzonSellerClient) -> int:
    products: list[dict] = []
    last_id = ""
    seen_product_cursors: set[str] = set()
    while True:
        payload = await client.product_attributes(last_id=last_id)
        raw_result = payload.get("result") or []
        items = raw_result.get("items", []) if isinstance(raw_result, dict) else raw_result
        products.extend(row for item in items if (row := product_catalog_row(item)))
        next_last_id = str(payload.get("last_id") or (raw_result.get("last_id") if isinstance(raw_result, dict) else "") or "")
        if len(items) < 1000 or not next_last_id or next_last_id in seen_product_cursors:
            break
        seen_product_cursors.add(next_last_id)
        last_id = next_last_id
    if products:
        replace_product_catalog(products)
    return len(products)


async def sync_inventory_source(client: OzonSellerClient) -> None:
    set_sync_state("inventory", "syncing", "Загружаем остатки FBO и текущие цены")
    product_count = 0
    try:
        product_count = await sync_product_catalog(client)
        set_sync_state("catalog", "ready", f"Карточки товаров: {product_count}", success=True)
    except Exception as error:
        # Product metadata enriches the staff table but must never block stock
        # and price updates used by the management dashboard.
        set_sync_state("catalog", "error", sync_error_detail(error))
        logger.warning("Ozon product catalog sync deferred: %s", type(error).__name__)
    try:
        prices: dict[str, float] = {}
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            payload = await client.product_prices(cursor=cursor)
            items = payload.get("items") or []
            for item in items:
                offer_id = str(item.get("offer_id") or "")
                if offer_id:
                    prices[offer_id] = current_price(item)
            next_cursor = str(payload.get("cursor") or "")
            if len(items) < 1000 or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        stocks: list[dict] = []
        offset = 0
        while True:
            payload = await client.warehouse_stocks(offset=offset)
            rows = payload.get("result", {}).get("rows", [])
            stocks.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000
        replace_inventory_snapshot(stocks, prices)
    except Exception as error:
        set_sync_state("inventory", "error", sync_error_detail(error))
        logger.warning("Ozon inventory sync deferred: %s", type(error).__name__)
        return
    set_sync_state("inventory", "ready", f"Остатки FBO: {len(stocks)} строк · карточки: {product_count}", success=True)


async def sync_finance_source(client: OzonSellerClient, today: date) -> None:
    desired_from = today - timedelta(days=92)
    earliest = finance_earliest_day()
    full_backfill = earliest is None or earliest > desired_from or finance_needs_sku_backfill()
    finance_from = desired_from if full_backfill else today - timedelta(days=14)
    finance_to = today - timedelta(days=1)
    set_sync_state("finance", "syncing", "Загружаем продажи, возвраты и расходы Ozon")
    try:
        operation_count = await sync_finance_data(client, finance_from, finance_to)
    except Exception as error:
        set_sync_state("finance", "error", sync_error_detail(error))
        logger.warning("Ozon finance sync deferred: %s", type(error).__name__)
        return
    set_sync_state("finance", "ready", f"Финансовые операции обновлены: {operation_count}", success=True)
    if full_backfill:
        set_sync_state("finance_units_v3", "ready", "Продажи и возвраты пересчитаны без служебных операций", success=True)
        set_sync_state("finance_funding_v1", "ready", "Займы и факторинг отделены от расходов Ozon", success=True)


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
