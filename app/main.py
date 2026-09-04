import asyncio
import hashlib
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
from app.services.storage import cost_template_products, dashboard as get_dashboard, import_cost_rows, initialise

ROOT = Path(__file__).resolve().parent.parent
scheduler = AsyncIOScheduler(timezone=settings.timezone)
basic_auth = HTTPBasic()
ONE_TIME_IMPORT_TOKEN_HASH = "e2dc6f04a42f45f74cdd25ff9b05782ac5cf573235a8988acdb0807a81bb938b"


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


@app.post("/api/admin/costs/import-once", include_in_schema=False)
async def costs_import_once(request: Request, x_import_token: str | None = Header(default=None)):
    """Temporary high-entropy, hash-verified importer; removed after this upload."""
    supplied_hash = hashlib.sha256((x_import_token or "").encode()).hexdigest()
    if not secrets.compare_digest(supplied_hash, ONE_TIME_IMPORT_TOKEN_HASH):
        raise HTTPException(404, "Not found")
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
