import logging
import re
from datetime import date, timedelta

from app.clients.ozon_seller import OzonSellerClient
from app.config import settings
from app.services.storage import upsert_daily_metric

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
    """Entry point for scheduled sync. Safe no-op until real keys are configured."""
    if not settings.sync_enabled or not settings.ozon_api_key or not settings.ozon_client_id:
        logger.info("Ozon sync skipped: configure new credentials and set SYNC_ENABLED=true")
        return
    today = date.today()
    payload = await OzonSellerClient().daily_analytics(today - timedelta(days=31), today - timedelta(days=1))
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
