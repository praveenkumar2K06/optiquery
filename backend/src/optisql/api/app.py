"""FastAPI Application factory and configuration for OptiQuery."""
from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .routes import router

def get_frontend_dist() -> Optional[Path]:
    for parent in Path(__file__).resolve().parents:
        cand = parent / "frontend" / "dist"
        if cand.exists() and (cand / "index.html").exists():
            return cand
    cwd_cand = Path.cwd() / "frontend" / "dist"
    if cwd_cand.exists() and (cwd_cand / "index.html").exists():
        return cwd_cand
    return None


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    frontend_dist = get_frontend_dist()

    app = FastAPI(
        title="OptiQuery SQL-to-NoSQL Compiler API",
        description=(
            "REST API for compiling SQL queries into optimized NoSQL targets "
            "(MongoDB aggregation pipelines and Redis commands) with a cost-based optimizer."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Enable CORS for external React dev servers (e.g. Vite on port 5173)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount Python API routes
    app.include_router(router)

    # Mount static assets if React build or static directory exists
    if frontend_dist and (frontend_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")
    elif STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def serve_spa(full_path: str):
        # Don't intercept API routes or documentation
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
            return HTMLResponse(status_code=404)

        if frontend_dist and (frontend_dist / "index.html").exists():
            return FileResponse(str(frontend_dist / "index.html"))

        if STATIC_DIR.exists():
            static_index = STATIC_DIR / "index.html"
            if static_index.exists():
                return FileResponse(str(static_index))

        return HTMLResponse(
            "<h1>OptiQuery API</h1><p>Visit <a href='/docs'>/docs</a> for the Swagger API documentation.</p>"
        )

    return app


app = create_app()
