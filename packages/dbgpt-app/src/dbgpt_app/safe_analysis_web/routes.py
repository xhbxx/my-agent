"""Mount a small browser workbench on the same DB-GPT server."""

from hashlib import blake2s
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(include_in_schema=False)
ASSETS = Path(__file__).with_name("assets")
DATABASE_ICON_DIR = ASSETS / "database-icons"
DATABASE_ICONS = frozenset(
    "mysql postgresql sqlite duckdb mssql clickhouse oracle oceanbase doris "
    "starrocks gaussdb opengauss vertica spark hive maxcompute tugraph neo4j".split()
)
PUBLIC_FILES = {
    "app.css",
    "api.js",
    "app.js",
    "boot.js",
    "management.js",
    "render.js",
    "stream.js",
    "favicon.svg",
    "marked.umd.js",
    "purify.min.js",
}


def _asset_revision() -> str:
    digest = blake2s(digest_size=8)
    for filename in sorted(PUBLIC_FILES):
        metadata = (ASSETS / filename).stat()
        digest.update(f"{filename}:{metadata.st_mtime_ns}:{metadata.st_size}\n".encode())
    return digest.hexdigest()


@router.get("/")
@router.get("/safe-analysis")
@router.get("/safe-analysis/")
async def workbench():
    revision = _asset_revision()
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        html.replace('"/safe-analysis/assets/', f'"/safe-analysis/assets/{revision}/'),
        headers={
            "Cache-Control": "no-store",
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
    return FileResponse(ASSETS / filename, headers={"Cache-Control": "no-store"})


@router.get("/safe-analysis/assets/{revision}/{filename}")
async def revisioned_asset(revision: str, filename: str):
    if filename not in PUBLIC_FILES or revision != _asset_revision():
        raise HTTPException(404)
    return FileResponse(
        ASSETS / filename, headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )


@router.get("/safe-analysis/database-icons/{kind}")
async def database_icon(kind: str):
    if kind in DATABASE_ICONS:
        path = DATABASE_ICON_DIR / f"{kind}.png"
        if path.is_file():
            return FileResponse(path)
    return FileResponse(DATABASE_ICON_DIR / "datasource.svg")
