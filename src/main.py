import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text
import uvicorn
from dotenv import load_dotenv

from src.services.database import engine, get_db_session
from src.api.auth import router as auth_router
from src.api.farm import router as farm_router

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_SRC_DIR    = Path(__file__).resolve().parent
_STATIC_DIR = _SRC_DIR / "static"
_KML_DIR    = _SRC_DIR / "data" / "kml"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("KVK GeoAI Demo starting up…")
    yield
    logger.info("Shutting down — disposing DB engine…")
    await engine.dispose()


app = FastAPI(
    title="KVK GeoAI Demo",
    description="Farm intelligence platform — login, geometry, spectral indices, advisory.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve KML files so the browser can fetch them if needed
if _KML_DIR.exists():
    app.mount("/kml", StaticFiles(directory=str(_KML_DIR)), name="kml")

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(farm_router, prefix="/api")


# ── Page routes ───────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def serve_login():
    return FileResponse(str(_STATIC_DIR / "login.html"))


@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    return FileResponse(str(_STATIC_DIR / "dashboard.html"))


@app.get("/admin", include_in_schema=False)
async def serve_admin():
    return FileResponse(str(_STATIC_DIR / "admin.html"))


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    from sqlalchemy.ext.asyncio import AsyncSession
    from src.services.database import async_session_factory
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)