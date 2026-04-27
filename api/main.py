"""
main.py
-------
Dexaview API server – entry point.

Starts a FastAPI application with the following route groups:
  /api/marketplace  – asset listing, purchasing, earnings
  /api/analytics    – watch-time events and aggregated stats
  /api/auth         – JWT-based user authentication

To start the development server:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Environment variables are loaded from a .env file in this directory.
See .env.example for all required keys.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db.session import create_tables
from api.routers import auth, marketplace, analytics
from api.core.config import settings

import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup and once on shutdown.
    On startup: initialises all database tables if they do not already exist.
    """
    await create_tables()
    yield
    # Teardown logic goes here (e.g. close connection pools)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Dexaview API",
    description=(
        "Backend for the Dexaview Industrial Metaverse. Handles marketplace "
        "transactions, creator earnings, and YouTube Education Player analytics."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS – allow the Vite dev server and the production domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router,        prefix="/api/auth",        tags=["auth"])
app.include_router(marketplace.router, prefix="/api/marketplace",  tags=["marketplace"])
app.include_router(analytics.router,   prefix="/api/analytics",    tags=["analytics"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health_check():
    """
    Simple liveness probe. Returns 200 OK if the server is running.
    Used by Docker health checks and load balancers.
    """
    return {"status": "ok", "version": app.version}

# ---------------------------------------------------------------------------
# Serve Frontend Static Files
# ---------------------------------------------------------------------------

# Mount the static assets (js, css, images) from the Vite build output
if os.path.exists("dist/assets"):
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")

# Catch-all route to serve the React app
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    # If the file exists in dist, return it (e.g., vite.svg, etc)
    dist_file = os.path.join("dist", full_path)
    if os.path.isfile(dist_file):
        return FileResponse(dist_file)
        
    index_file = os.path.join("dist", "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
        
    return {"error": "Frontend build not found. Please run 'npm run build' first."}
