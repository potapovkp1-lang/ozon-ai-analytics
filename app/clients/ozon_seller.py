"""Read-only Ozon Seller API client.

Endpoints are intentionally concentrated here so that version changes in Ozon
documentation can be updated without touching analytics logic.
"""
import httpx
from datetime import date

from app.config import settings


class OzonSellerClient:
    base_url = "https://api-seller.ozon.ru"

    @property
    def headers(self) -> dict[str, str]:
        return {"Client-Id": settings.ozon_client_id, "Api-Key": settings.ozon_api_key}

    async def post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(f"{self.base_url}{path}", headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()

    async def product_list(self, limit: int = 1000) -> dict:
        return await self.post("/v3/product/list", {"filter": {"visibility": "ALL"}, "limit": limit, "last_id": ""})

    async def stock_list(self, product_ids: list[int]) -> dict:
        return await self.post("/v4/product/info/stocks", {"filter": {"product_id": product_ids}, "limit": 1000})

    async def daily_analytics(self, date_from: date, date_to: date) -> dict:
        """Daily revenue and order-units series from Seller Analytics API."""
        return await self.post("/v1/analytics/data", {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "metrics": ["revenue", "ordered_units", "canceled_units"],
            "dimension": ["day"],
            "filters": [],
            "sort": [{"key": "day", "order": "ASC"}],
            "limit": 1000,
            "offset": 0,
        })
