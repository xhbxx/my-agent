"""Mount a small browser workbench on the same DB-GPT server."""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(include_in_schema=False)
ASSETS = Path(__file__).with_name("assets")
PUBLIC_FILES = {
    "app.css",
    "api.js",
    "app.js",
    "render.js",
    "stream.js",
    "favicon.svg",
    "marked.umd.js",
    "purify.min.js",
}


@router.get("/safe-analysis")
@router.get("/safe-analysis/")
async def workbench():
    return FileResponse(
        ASSETS / "index.html",
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
                "frame-ancestors 'self'"
            ),
        },
    )


@router.get("/safe-analysis/assets/{filename}")
async def asset(filename: str):
    if filename not in PUBLIC_FILES:
        raise HTTPException(404)
    return FileResponse(ASSETS / filename)


def mount_home(app, static_path):
    """Enabled deployments open the workbench; the original home stays reachable."""
    if not os.environ.get("DATA_AGENT_CONFIG"):
        return

    @app.get("/", include_in_schema=False)
    async def home(request: Request):
        if request.query_params.get("classic") == "1":
            return FileResponse(Path(static_path) / "index.html")
        return RedirectResponse("/safe-analysis/")
