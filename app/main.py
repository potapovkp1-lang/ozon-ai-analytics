import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.services.sync import sync_operational_data
from app.services.storage import dashboard, initialise

ROOT = Path(__file__).resolve().parent.parent
scheduler = AsyncIOScheduler(timezone=settings.timezone)


def gpt_authorized(authorization: str | None = Header(default=None)) -> None:
    if not settings.gpt_action_token:
        raise HTTPException(503, "GPT Action token is not configured")
    expected = f"Bearer {settings.gpt_action_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid GPT Action token")


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


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "sync_enabled": settings.sync_enabled}


@app.get("/api/v1/dashboard", dependencies=[Depends(gpt_authorized)])
async def dashboard_data():
    return dashboard()


@app.get("/api/v1/brief", dependencies=[Depends(gpt_authorized)])
async def executive_brief():
    data = dashboard()
    return {"period_days": data["period_days"], "summary": "Данные Ozon синхронизируются ежедневно.", "kpis": data["kpis"], "actions": data["insights"]}
