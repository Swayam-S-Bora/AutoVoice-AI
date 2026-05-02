from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from app.api.routes import router
from app.services.speech_service import warm_phrase_cache
from app.core.logger import app_logger
from app.core.config import settings, validate_settings
import asyncio
import os

# Validate secrets before anything else (exits cleanly if missing)
validate_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_logger.info("Starting TTS phrase cache warmup...")
    warm_task = asyncio.create_task(warm_phrase_cache())
    app_logger.info("AutoVoice-AI ready.")
    try:
        yield
    finally:
        if not warm_task.done():
            warm_task.cancel()


app = FastAPI(lifespan=lifespan)

# CORS — only allow configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)

# Serve frontend
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")


@app.get("/")
async def index():
    return FileResponse(os.path.join(frontend_dir, "index.html"))


@app.head("/")
async def index_head():
    return Response(status_code=200)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.head("/healthz", include_in_schema=False)
async def healthz_head():
    return Response(status_code=200)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    ico_path = os.path.join(frontend_dir, "favicon.ico")
    return FileResponse(
        ico_path,
        media_type="image/x-icon",
        headers={"Cache-Control": "no-cache"},
    )

@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_config():
    return {}
