import httpx

from app.config import settings


class OzonPerformanceClient:
    token_url = "https://api-performance.ozon.ru/api/client/token"

    async def access_token(self) -> str:
        """Obtain OAuth token without logging credentials or token values."""
        payload = {
            "client_id": settings.ozon_performance_client_id,
            "client_secret": settings.ozon_performance_client_secret,
            "grant_type": "client_credentials",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self.token_url, data=payload)
            response.raise_for_status()
            return response.json()["access_token"]
