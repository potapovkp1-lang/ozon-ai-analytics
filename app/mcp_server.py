import secrets
from datetime import date
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.services.storage import dashboard, photo_analytics


mcp_server = MCPServer(
    "Ozon AI Analytics",
    instructions=(
        "Read-only access to stored Ozon Seller and analytics data. "
        "The tools never change campaigns, bids, products, prices, or stock."
    ),
)


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format") from error


def _period(days: int, date_from: str | None, date_to: str | None) -> tuple[int, date | None, date | None]:
    if not 1 <= days <= 3650:
        raise ValueError("days must be between 1 and 3650")
    return days, _parse_date(date_from, "date_from"), _parse_date(date_to, "date_to")


@mcp_server.tool()
def get_ozon_dashboard(
    days: int = 30,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Return stored shop KPIs, sales, stock, finance, and data-quality details.

    Use either ``days`` or an inclusive ``date_from``/``date_to`` period in
    YYYY-MM-DD format. The response contains only data already collected by
    this service from Ozon Seller API sources.
    """
    period_days, start, end = _period(days, date_from, date_to)
    return dashboard(days=period_days, date_from=start, date_to=end)


@mcp_server.tool()
def get_ozon_product_funnel(
    days: int = 30,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str = "",
) -> dict[str, Any]:
    """Return stored per-product impressions, views, carts, orders, and buyouts.

    ``search`` optionally filters by article, SKU, product name, size, or
    barcode. Dates use YYYY-MM-DD. These are Seller API funnel metrics, not
    advertising spend or Performance API campaign metrics.
    """
    if len(search) > 200:
        raise ValueError("search must be at most 200 characters")
    period_days, start, end = _period(days, date_from, date_to)
    return photo_analytics(
        days=period_days,
        date_from=start,
        date_to=end,
        search=search,
    )


class BearerTokenMiddleware:
    """Small raw-ASGI guard that does not buffer MCP streaming responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not settings.gpt_action_token:
            response = JSONResponse(
                {"error": "MCP token is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        authorization = Headers(scope=scope).get("authorization", "")
        expected = f"Bearer {settings.gpt_action_token}"
        if not secrets.compare_digest(authorization, expected):
            response = JSONResponse(
                {"error": "Invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


allowed_hosts = [
    host.strip()
    for host in settings.mcp_allowed_hosts.split(",")
    if host.strip()
]
transport_security = TransportSecuritySettings(allowed_hosts=allowed_hosts)
mcp_app = BearerTokenMiddleware(
    mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )
)
