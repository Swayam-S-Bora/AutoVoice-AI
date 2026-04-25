from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api.routes import router
from app.services.speech_service import warm_phrase_cache
from app.core.logger import app_logger
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_logger.info("Warming TTS phrase cache...")
    await warm_phrase_cache()
    app_logger.info("AutoVoice-AI ready.")
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(router)

# Serve static frontend
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))
