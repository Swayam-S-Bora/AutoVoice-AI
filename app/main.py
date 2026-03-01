from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from app.routers import customers, appointments, voice

# Import our modules
from app.database import supabase
from app.logger import app_logger, call_logger
from app.routers import customers, appointments

# Load environment variables
load_dotenv()

from contextlib import asynccontextmanager

# Lifespan handler replacing deprecated on_event
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup actions
    app_logger.info("=" * 50)
    app_logger.info("AutoVoice AI Application Starting")
    app_logger.info("=" * 50)
    yield
    # shutdown actions
    app_logger.info("Application shutting down")

# Initialize FastAPI with lifespan
app = FastAPI(
    title="AutoVoice AI",
    description="AI-Powered Voice Agent for Automobile Dealerships",
    version="1.0.0",
    lifespan=lifespan
)

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(customers.router)
app.include_router(appointments.router)
app.include_router(voice.router)

@app.get("/")
async def root():
    app_logger.debug("Root endpoint accessed")
    return {
        "message": "Welcome to AutoVoice AI API",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    app_logger.info("Health check requested")
    try:
        # Test database connection
        result = supabase.table("customers").select("*", count="exact").limit(1).execute()
        app_logger.info("Database connection successful")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        app_logger.error(f"Database connection failed: {str(e)}")
        return {"status": "unhealthy", "database": str(e)}
