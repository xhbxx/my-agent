"""本机设置与入库控制接口，不返回API密钥。"""

from contextlib import closing
import json
import sqlite3
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app import config
from app.knowledge.jobs import IndexBuild
from app.paths import BASE_DIR, STORE_DIR

build = IndexBuild()
_mutation_lock = threading.Lock()


def local_request(request: Request, response: Response):
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "请使用本机地址打开设置页。")
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin != expected:
        raise HTTPException(403, "请在本机应用页面修改设置。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请在本机应用页面修改设置。")
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api", dependencies=[Depends(local_request)])


def index_ready():
    catalog = STORE_DIR / "catalog.db"
    if not catalog.exists():
        return False
    try:
        with closing(sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT version FROM active WHERE id=1").fetchone()
        if row is None:
            return False
        manifest = json.loads(
            (STORE_DIR / "snapshots" / row[0] / "passages.json").read_text(encoding="utf-8")
        )
        return manifest["embedding_id"] == config.embedding_identity()
    except (sqlite3.Error, OSError, ValueError, KeyError):
        return False


def settings_status():
    return {
        "deepseek_configured": config.configured("DEEPSEEK_API_KEY"),
        "siliconflow_configured": config.configured("SILICONFLOW_API_KEY"),
        "index_ready": index_ready(),
        "build": build.snapshot(),
        "models": {
            name: {
                "base_url": config.service_settings(name)["base_url"],
                "model": config.service_settings(name)["model"],
                "api_key_configured": config.service_configured(name),
            }
            for name in config.SERVICES
        },
    }


@router.get("/settings")
def get_settings():
    return settings_status()


@router.put("/settings")
async def update_settings(request: Request):
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(413, "配置内容过长。")
    try:
        values = json.loads(body)
        if not isinstance(values, dict):
            raise ValueError
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError
    except (ValueError, UnicodeError):
        raise HTTPException(400, "请填写单行API密钥。") from None
    updates = {}
    for name in config.SERVICES:
        for field in ("base_url", "model", "api_key"):
            value = values.get(f"{name}_{field}", "").strip()
            if value:
                updates[f"{name.upper()}_{field.upper()}"] = value
    for field, name in (
        ("deepseek_api_key", "DEEPSEEK_API_KEY"),
        ("siliconflow_api_key", "SILICONFLOW_API_KEY"),
    ):
        value = values.get(field, "").strip()
        if value:
            updates[name] = value
    with _mutation_lock:
        if build.snapshot()["running"]:
            raise HTTPException(409, "知识库正在建立，请完成后再修改密钥。")
        try:
            config.save_keys(updates)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        config.reset_clients()
    return settings_status()


@router.post("/knowledge/build", status_code=202)
def start_build():
    with _mutation_lock:
        if not config.service_configured("embedding"):
            raise HTTPException(400, "请先保存向量模型的API地址、模型名称和密钥。")
        try:
            return build.start(BASE_DIR / "data")
        except ValueError as error:
            raise HTTPException(400, str(error)) from error


@router.get("/knowledge/build")
def build_status():
    return {"index_ready": index_ready(), **build.snapshot()}
