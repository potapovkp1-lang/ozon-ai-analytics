import logging

from app.clients.ozon_seller import OzonSellerClient
from app.config import settings

logger = logging.getLogger(__name__)


async def sync_operational_data() -> None:
    """Entry point for scheduled sync. Safe no-op until real keys are configured."""
    if not settings.sync_enabled or not settings.ozon_api_key or not settings.ozon_client_id:
        logger.info("Ozon sync skipped: configure new credentials and set SYNC_ENABLED=true")
        return
    products = await OzonSellerClient().product_list()
    logger.info("Ozon sync completed: received product batch", extra={"count": len(products.get("items", []))})
    # Next implementation: normalise product, postings, finance and ad-report data into storage.
