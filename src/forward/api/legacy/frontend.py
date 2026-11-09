"""Serve the built web app."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

logger = logging.getLogger("forward.api")


# ----------------------------------------------------------------------
# Frontend (built React app) — API routes above take precedence.
# ----------------------------------------------------------------------
_WEB_DIST = Path(__file__).resolve().parents[4] / "web" / "dist"


class BuildAssets(StaticFiles):
    """Vite's content-hashed assets can be reused until their URL changes."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304) and re.search(
            r"-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+$", path
        ):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_frontend(app: FastAPI) -> None:
    """Mount the built SPA if present. Call last: its catch-all would swallow later routes."""
    assets = _WEB_DIST / "assets"
    if assets.is_dir():
        # Compress static bundles only; live API data and streaming responses
        # retain their existing behavior. The HTML shell remains no-store.
        app.mount(
            "/assets",
            GZipMiddleware(BuildAssets(directory=assets), minimum_size=1024, compresslevel=5),
            name="assets",
        )
    if (_WEB_DIST / "index.html").exists():
        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        def spa(path: str) -> FileResponse | JSONResponse:
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            # Resolve before serving: `%2e%2e` decodes to `..` and would otherwise
            # walk out of the dist folder to `.env` or the broker session file.
            root = _WEB_DIST.resolve()
            candidate = (root / path).resolve()
            if path and candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            # The shell references hashed assets (safe to cache); the shell
            # itself must never be cached, or the browser keeps booting the
            # previous bundle after a rebuild.
            return FileResponse(
                _WEB_DIST / "index.html",
                headers={"Cache-Control": "no-store"},
            )

