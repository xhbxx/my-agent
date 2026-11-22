"""Thin HTTP adapter. All business decisions live in safe_analysis/service.py."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dbgpt_serve.safe_analysis.contracts import AnalysisError

from .workbench_access import workbench_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/safe-analysis")


class AnalysisRequest(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    question: str = Field(default="", max_length=4000)
    sql: str | None = Field(default=None, max_length=20000)
    approval_id: str | None = Field(default=None, max_length=100)
    skill: str = "general"
    explain: bool = True
    stream: bool = True


async def get_service(request: Request):
    if not hasattr(request.app.state, "safe_analysis_lock"):
        request.app.state.safe_analysis_lock = asyncio.Lock()
    async with request.app.state.safe_analysis_lock:
        service = getattr(request.app.state, "safe_analysis_service", None)
        if service is None:
            try:
                from dbgpt._private.config import Config
                from dbgpt_serve.safe_analysis.runtime import build_service

                service = await asyncio.to_thread(build_service, Config().SYSTEM_APP)
                request.app.state.safe_analysis_service = service
            except AnalysisError as exc:
                logger.warning("Safe analysis configuration: %s", exc)
                raise HTTPException(503, str(exc)) from None
            except Exception:
                # Avoid logging config/driver exceptions which may contain credentials.
                logger.warning(
                    "Safe analysis initialization failed; check its configuration"
                )
                raise HTTPException(503, "安全分析未启用或配置不完整") from None
    return service


async def get_principal(
    request: Request,
    authorization: str = Header(default=""),
    service=Depends(get_service),
):
    token = workbench_token(request, authorization)
    if not token:
        raise HTTPException(401, "请用一键启动文件启动，然后在本机打开工作台")
    try:
        return service.settings.authenticate(token)
    except AnalysisError:
        raise HTTPException(401, "访问令牌无效") from None


@router.get("/sources")
async def sources(
    request: Request,
    service=Depends(get_service),
    principal=Depends(get_principal),
    authorization: str = Header(default=""),
):
    from dbgpt_serve.safe_analysis.guard import visible_schema
    from dbgpt_serve.safe_analysis.management import is_manager

    names = [name for name in principal.sources if name in service.settings.sources]
    return {
        "sources": names,
        "can_manage": is_manager(workbench_token(request, authorization)),
        "model": (
            service.model_settings.public(service.settings.model)["model"]
            if getattr(service, "model_settings", None)
            else service.settings.model
        ),
        "details": [
            {
                "name": name,
                "dialect": service.settings.sources[name].dialect,
                "tables": visible_schema(service.settings.sources[name]),
                "metrics": service.settings.sources[name].metrics,
                "documents": [
                    Path(filename).name
                    for filename in service.retrievers[name].document_paths()
                ],
                "retrieval": (
                    "Qdrant + BM25 + RRF"
                    if service.settings.sources[name].vector and service.qdrant_path
                    else "向量检索 + BM25 + RRF"
                    if service.settings.sources[name].vector
                    else "BM25"
                ),
                "index_ready": service.retrievers[name].index_ready,
                "can_upload": service.retrievers[name].knowledge_path is not None,
            }
            for name in names
        ],
    }


@router.get("/documents")
async def document(
    source: str,
    filename: str,
    service=Depends(get_service),
    principal=Depends(get_principal),
):
    try:
        service.settings.authorize(principal, source)
    except AnalysisError:
        raise HTTPException(403, "没有此数据源的访问权限") from None
    prefix = f"来源: {filename}\n"
    excerpts = [
        item.content.removeprefix(prefix)
        for item in service.retrievers[source].documents
        if item.kind == "document" and item.content.startswith(prefix)
    ]
    if not excerpts:
        raise HTTPException(404, "这个文档没有可预览的内容")
    return {
        "filename": filename,
        "excerpt": excerpts[0],
        "truncated": len(excerpts) > 1,
    }


@router.post("/context")
async def context(
    body: AnalysisRequest,
    service=Depends(get_service),
    principal=Depends(get_principal),
):
    from dataclasses import asdict

    try:
        service.settings.authorize(principal, body.source)
    except AnalysisError as exc:
        raise HTTPException(403, str(exc)) from None
    try:
        docs = await service.context(principal, body.source, body.question)
        return {"evidence": [asdict(doc) for doc in docs]}
    except AnalysisError as exc:
        raise HTTPException(503, str(exc)) from None
    except Exception:
        raise HTTPException(503, "检索服务暂不可用") from None


@router.post("/query")
async def query(
    body: AnalysisRequest,
    service=Depends(get_service),
    principal=Depends(get_principal),
):
    try:
        service.settings.authorize(principal, body.source)
        from dbgpt_serve.safe_analysis.skills import get_skill

        get_skill(body.skill)
        if not body.sql and not body.question.strip():
            raise AnalysisError("请提供问题或 SQL")
        if body.approval_id and not body.sql:
            raise AnalysisError("确认时必须携带原 SQL")
    except AnalysisError as exc:
        raise HTTPException(400, str(exc)) from None

    async def events():
        try:
            async for item in service.analyze(
                principal,
                body.source,
                body.question,
                body.sql,
                body.approval_id,
                body.skill,
                body.explain,
            ):
                yield item
        except AnalysisError as exc:
            yield {"event": "error", "data": {"message": str(exc)}}
        # A finite terminal marker even on clarification/confirmation/error.
        yield {"event": "done", "data": {}}

    if not body.stream:
        return {"events": [item async for item in events()]}

    async def sse():
        async for item in events():
            data = json.dumps(item["data"], ensure_ascii=False, allow_nan=False)
            yield f"event: {item['event']}\ndata: {data}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/requests/{request_id}/cancel")
async def cancel(
    request_id: str,
    service=Depends(get_service),
    principal=Depends(get_principal),
):
    try:
        service.cancel(principal, request_id)
        return {"cancel_requested": True}
    except AnalysisError as exc:
        raise HTTPException(404, str(exc)) from None


def close_service(app):
    service = getattr(app.state, "safe_analysis_service", None)
    if service:
        service.close()
