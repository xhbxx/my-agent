"""Workbench authorization and presentation around native data source management."""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError

from dbgpt_serve.datasource.api.endpoints import get_service as native_datasources
from dbgpt_serve.datasource.api.schemas import DatasourceCreateRequest
from dbgpt_serve.safe_analysis.contracts import AnalysisError
from dbgpt_serve.safe_analysis.management import ModelConnection, complete, is_manager
from dbgpt_serve.safe_analysis.retrieval import MAX_DOCUMENT_BYTES
from dbgpt_serve.safe_analysis.retrieval_models import probe_retrieval_model

from .safe_analysis_api import get_principal, get_service
from .workbench_access import workbench_token


async def require_manager(request: Request, authorization: str = Header(default="")):
    if not is_manager(workbench_token(request, authorization)):
        raise HTTPException(403, "当前令牌没有工作台管理权限")


router = APIRouter(
    prefix="/v1/safe-analysis/manage", dependencies=[Depends(require_manager)]
)


def _model_store(service, kind):
    if kind == "chat":
        return service.model_settings, service.settings.model
    store = getattr(service, "retrieval_models", {}).get(kind)
    if store is None:
        raise HTTPException(404, "模型类别不可用")
    return store, ""


@router.get("/model")
async def model_settings(request: Request, response: Response, kind: str = "chat"):
    service = await get_service(request)
    store, default = _model_store(service, kind)
    response.headers["Cache-Control"] = "no-store"
    return store.public(default)


@router.post("/model")
async def configure_model(
    request: Request, test_only: bool = False, kind: str = "chat"
):
    service = await get_service(request)
    store, default = _model_store(service, kind)
    if kind == "embedding" and not service.qdrant_path:
        raise HTTPException(400, "向量模型覆盖需要先配置工作台 Qdrant 存储")
    try:
        submitted = ModelConnection.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise HTTPException(400, "模型配置格式无效，请检查输入字段") from None
    try:
        config = store.prepare(submitted)
        if kind == "chat":
            await complete(config, "", "Reply with OK.", probe=True)
        else:
            await asyncio.to_thread(probe_retrieval_model, kind, config)
        if not test_only:
            store.save(config)
    except AnalysisError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(500, "无法保存模型设置，请检查服务端数据目录权限") from None
    return {"ok": True, **store.public(default)}


@router.delete("/model")
async def reset_model(request: Request, kind: str = "chat"):
    service = await get_service(request)
    store, default = _model_store(service, kind)
    try:
        store.reset()
    except OSError:
        raise HTTPException(500, "无法删除本地模型设置") from None
    return store.public(default)


@router.post("/knowledge")
async def upload_knowledge(
    request: Request,
    source: str,
    filename: str,
    service=Depends(get_service),
    principal=Depends(get_principal),
):
    try:
        service.settings.authorize(principal, source)
    except AnalysisError:
        raise HTTPException(403, "没有此数据源的访问权限") from None
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "请从知识库页面选择文件")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_DOCUMENT_BYTES:
            raise HTTPException(413, "文件不能超过 10 MB")
        data.extend(chunk)
    try:
        await asyncio.to_thread(service.retrievers[source].add_document, filename, data)
    except AnalysisError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(500, "文档保存失败，请检查服务端存储") from None
    return {"filename": filename}


def _secret(name, field):
    tags = (field.get("ext_metadata") or {}).get("tags") or []
    return "privacy" in tags or any(
        word in name.lower()
        for word in ("password", "passwd", "pwd", "secret", "token", "api_key")
    )


def _public_params(values, fields):
    public = {}
    for field in fields:
        name = field["param_name"]
        if name not in values or _secret(name, field):
            continue
        value = values[name]
        if field["nested_fields"] and isinstance(value, dict):
            kind = value.get("type")
            value = {
                **_public_params(value, field["nested_fields"].get(kind, [])),
                "type": kind,
            }
        public[name] = value
    return public


def _public_fields(fields):
    for field in fields:
        if _secret(field["param_name"], field):
            field["default_value"] = None
        for nested in (field.get("nested_fields") or {}).values():
            _public_fields(nested)


def _datasource_types(service):
    return service.datasource_types().model_dump()["types"]


def _list_data():
    service = native_datasources()
    types = _datasource_types(service)
    by_type = {item["name"]: item["parameters"] for item in types}
    connections = [
        {
            "id": item.id,
            "name": item.db_name,
            "type": item.type,
            "description": item.description,
            "params": _public_params(item.params, by_type.get(item.type, [])),
        }
        for item in service.get_list()
    ]
    for item in types:
        _public_fields(item["parameters"])
    return {"types": types, "connections": connections}


def _restore_params(old, new):
    result = dict(old)
    for key, value in new.items():
        previous = old.get(key)
        if (
            isinstance(previous, dict)
            and isinstance(value, dict)
            and previous.get("type") == value.get("type")
        ):
            value = _restore_params(previous, value)
        result[key] = value
    return result


def _save_connection(body, test_only):
    service = native_datasources()
    if body.id is not None:
        current = service.get(str(body.id))
        if not current or current.type != body.type:
            raise HTTPException(404, "连接不存在，或数据库类型已变化")
        body.params = _restore_params(current.params, body.params)
        # The native update looks records up by database name, not by ID.
        params = service.datasource_manager._create_parameters(body)
        if params.persisted_state().get("db_name") != current.db_name:
            raise HTTPException(400, "编辑不能改连接名称；请新建连接后再删除旧连接")
    if not service.test_connection(body):
        raise HTTPException(400, "连接测试未通过")
    if not test_only:
        (service.update if body.id is not None else service.create)(body)


async def _native(fn, error, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except HTTPException as exc:
        # Only our own fixed messages above are safe to return; the native
        # connector may include driver errors or credentials in its exceptions.
        if exc.detail in (
            "连接不存在，或数据库类型已变化",
            "编辑不能改连接名称；请新建连接后再删除旧连接",
            "连接测试未通过",
        ):
            raise
        raise HTTPException(exc.status_code, error) from None
    except Exception:
        raise HTTPException(400, error) from None


@router.get("/datasources")
async def list_connections(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await _native(_list_data, "无法读取数据源，请检查服务端连接配置")


@router.post("/datasources")
async def save_connection(request: Request, test_only: bool = False):
    try:
        body = DatasourceCreateRequest.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise HTTPException(400, "数据源配置格式无效") from None
    await _native(
        _save_connection,
        "操作未完成，请检查驱动、连接参数及名称是否重复",
        body,
        test_only,
    )
    return {"ok": True}


@router.post("/datasources/{connection_id}/refresh")
async def refresh_connection(connection_id: int):
    await _native(
        lambda: native_datasources().refresh(str(connection_id)), "刷新连接失败"
    )
    return {"ok": True}


@router.delete("/datasources/{connection_id}")
async def delete_connection(connection_id: int):
    await _native(
        lambda: native_datasources().delete(str(connection_id)), "删除连接失败"
    )
    return {"ok": True}
