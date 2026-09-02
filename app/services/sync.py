import logging
from datetime import date, timedelta

from app.clients.ozon_seller import OzonSellerClient
from app.config import settings
from app.services.storage import upsert_daily_metric

logger = logging.getLogger(__name__)


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
            day = date.fromisoformat(str(dimensions[0].get("id") or dimensions[0].get("name"))[:10])
            upsert_daily_metric(day, float(metrics[0] or 0), int(float(metrics[1] or 0)), int(float(metrics[2] or 0)))
        except (TypeError, ValueError, IndexError):
            logger.warning("Skipped analytics row with unexpected Ozon format")
    logger.info("Ozon analytics sync completed")
