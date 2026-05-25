"""
E-Huzanya Gateway — Application Entry Point
=============================================

Start the server:
    uvicorn app.main:app --reload --port 8000

Access Swagger UI:
    http://localhost:8000/docs

Access ReDoc:
    http://localhost:8000/redoc
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db.session import init_db
from app.api.routes import router
from app.api.websocket import router as ws_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables on startup."""
    await init_db()
    print("✓ Database initialized")
    print("✓ E-Huzanya Gateway is operational")
    print("=" * 50)
    print("  Swagger UI:  http://localhost:8000/docs")
    print("  ReDoc:       http://localhost:8000/redoc")
    print("=" * 50)
    yield
    print("E-Huzanya Gateway shutting down")


app = FastAPI(
    title="E-Huzanya Gateway",
    description=(
        "API-first interoperability middleware connecting Rwanda's "
        "National Police (112) and SAMU/e-Banguka (912) emergency dispatch systems. "
        "Translates, routes, and secures real-time incident data between agencies "
        "using a Canonical Intermediate Representation and a Clinical Firewall."
    ),
    version="0.1.0-mvp",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the React frontend to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP: allow all. Production: restrict to frontend domain.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routes
app.include_router(router)
app.include_router(ws_router)


# Health check (no auth required)
@app.get("/health")
async def health_check():
    return {
        "status": "operational",
        "service": "E-Huzanya Gateway",
        "version": "0.1.0-mvp",
    }


# Serve the tactical dashboard
@app.get("/")
async def serve_dashboard():
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
