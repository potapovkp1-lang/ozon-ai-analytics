import asyncio
import csv
import io
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.services.costs import parse_cost_csv, template_csv
from app.services.sync import sync_operational_data
from app.services.storage import (
    cost_template_products,
    dashboard as get_dashboard,
    import_cost_rows,
    initialise,
    photo_analytics as get_photo_analytics,
)

ROOT = Path(__file__).resolve().parent.parent
scheduler = AsyncIOScheduler(timezone=settings.timezone)
basic_auth = HTTPBasic()


def gpt_authorized(authorization: str | None = Header(default=None)) -> None:
    if not settings.gpt_action_token:
        raise HTTPException(503, "GPT Action token is not configured")
    expected = f"Bearer {settings.gpt_action_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid GPT Action token")


def dashboard_authorized(credentials: HTTPBasicCredentials = Depends(basic_auth)) -> None:
    if not settings.dashboard_username or not settings.dashboard_password:
        raise HTTPException(503, "Dashboard credentials are not configured")
    is_valid = secrets.compare_digest(credentials.username, settings.dashboard_username) and secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect dashboard credentials", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.database_url:
        initialise()
    scheduler.add_job(sync_operational_data, "interval", hours=1, id="operational-sync", replace_existing=True)
    scheduler.start()
    if settings.sync_enabled:
        asyncio.create_task(sync_operational_data())
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Ozon AI Analytics API", version="0.1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")


@app.get("/", include_in_schema=False, dependencies=[Depends(dashboard_authorized)])
async def dashboard_page():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "sync_enabled": settings.sync_enabled}


@app.get("/api/public/dashboard", dependencies=[Depends(dashboard_authorized)])
async def public_dashboard_data(
    days: int = Query(default=30, ge=1, le=3650),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    """Aggregate shop metrics for the visual dashboard; GPT endpoints stay protected."""
    try:
        return get_dashboard(days=days, date_from=date_from, date_to=date_to)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/public/photo-analytics", dependencies=[Depends(dashboard_authorized)])
async def public_photo_analytics(
    days: int = Query(default=30, ge=1, le=3650),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    search: str = Query(default="", max_length=200),
):
    try:
        return get_photo_analytics(days=days, date_from=date_from, date_to=date_to, search=search)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/public/photo-analytics.csv", dependencies=[Depends(dashboard_authorized)])
async def public_photo_analytics_csv(
    days: int = Query(default=30, ge=1, le=3650),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    search: str = Query(default="", max_length=200),
):
    try:
        data = get_photo_analytics(days=days, date_from=date_from, date_to=date_to, search=search)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Артикул", "Ozon SKU", "Наименование", "Размер", "ШК",
        "Показы в поиске и каталоге", "Просмотры карточки", "CTR, %",
        "Поиск/каталог → карточка, %", "Карточка → избранное, %",
        "Карточка → корзина, %", "Корзина → заказ, %", "Заказ → выкуп, %",
        "Добавлено в корзину", "Заказано, шт.", "Выкуплено, шт.",
    ])
    for row in data["rows"]:
        writer.writerow([
            row["offer_id"], row["ozon_sku"], row["product_name"], row["size"], row["barcode"],
            row["search_catalog_impressions"], row["views"], row["ctr"],
            row["search_catalog_to_card"], row["card_to_favorite"], row["card_to_cart"],
            row["cart_to_order"], row["order_to_buyout"], row["cart_additions"],
            row["ordered_units"], row["sold_units"],
        ])
    filename = f"photo-analytics-{data['period']['date_from']}-{data['period']['date_to']}.csv"
    return Response(
        content=("\ufeff" + output.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/v1/dashboard", dependencies=[Depends(gpt_authorized)])
async def dashboard_data(
    days: int = Query(default=30, ge=1, le=3650),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    try:
        return get_dashboard(days=days, date_from=date_from, date_to=date_to)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/v1/brief", dependencies=[Depends(gpt_authorized)])
async def executive_brief(
    days: int = Query(default=30, ge=1, le=3650),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    try:
        data = get_dashboard(days=days, date_from=date_from, date_to=date_to)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"period": data["period"], "summary": "Управленческая аналитика Ozon за выбранный период.", "kpis": data["kpis"], "actions": data["insights"], "data_quality": data["data_quality"]}


@app.get("/api/admin/costs/template", dependencies=[Depends(dashboard_authorized)])
async def costs_template():
    return Response(
        content=template_csv(cost_template_products()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ozon-costs-template.csv"'},
    )


@app.post("/api/admin/costs/import", dependencies=[Depends(dashboard_authorized)])
async def costs_import(request: Request):
    raw = await request.body()
    try:
        rows = parse_cost_csv(
            raw,
            default_purchase_vat=settings.default_purchase_vat_rate,
            default_sale_vat=settings.default_sale_vat_rate,
        )
        imported = import_cost_rows(rows)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"status": "ok", "imported": imported}
