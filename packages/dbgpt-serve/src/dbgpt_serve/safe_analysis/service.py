"""READ THIS FIRST: the complete analysis flow in one explicit async generator.

authorize -> retrieve -> generate -> validate -> EXPLAIN -> confirm/revalidate
          -> read-only execute -> sanitize -> result -> optional explanation
"""

import asyncio
import hashlib
import threading
import uuid
from dataclasses import asdict

from .audit import AuditStore
from .contracts import AnalysisError
from .executor import ReadOnlyExecutor
from .guard import validate_sql
from .model import DataAgentModel
from .results import chart_spec, sanitize
from .retrieval import ContextRetriever
from .skills import get_skill


class AnalysisService:
    def __init__(
        self,
        settings,
        system_app=None,
        model=None,
        *,
        retrieval_models=None,
        knowledge_root=None,
    ):
        self.settings = settings
        self.audit = AuditStore(settings.audit_path)
        self.model = model or DataAgentModel(system_app, settings.model)
        self.executors = {}
        self.retrievers = {}
        self.active = {}
        self.slots = asyncio.Semaphore(16)
        self.qdrant_path = getattr(settings, "qdrant_path", None)
        self.retrieval_models = retrieval_models or {}

        def vector_factory(name, embeddings=None):
            if self.qdrant_path:
                from dbgpt.rag.embedding import EmbeddingFactory
                from dbgpt_ext.storage.vector_store.qdrant_store import (
                    QdrantStore,
                    QdrantVectorConfig,
                )

                # A directory per policy index avoids two clients opening the
                # same local database. Local mode is for a single web worker.
                if embeddings is None:
                    embeddings = system_app.get_component(
                        "embedding_factory", EmbeddingFactory
                    ).create()
                return QdrantStore(
                    QdrantVectorConfig(path=str(self.qdrant_path / name)),
                    name=name,
                    embedding_fn=embeddings,
                )
            from dbgpt_serve.rag.storage_manager import StorageManager

            return StorageManager.get_instance(system_app).create_vector_store(name)

        try:
            for name, policy in settings.sources.items():
                self.executors[name] = ReadOnlyExecutor(policy)
                self.retrievers[name] = ContextRetriever(
                    policy,
                    vector_factory if system_app is not None else None,
                    owns_store=bool(self.qdrant_path),
                    model_settings=self.retrieval_models,
                    knowledge_path=(
                        knowledge_root
                        / hashlib.sha256(
                            f"{settings.path}\n{name}\n{policy.url}\n{policy.schema}".encode()
                        ).hexdigest()[:32]
                        if knowledge_root is not None
                        else None
                    ),
                )
        except Exception:
            self.close()
            raise

    async def context(self, principal, source, question):
        self.settings.authorize(principal, source)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.retrievers[source].retrieve, question), 45
            )
        except asyncio.TimeoutError as exc:
            raise AnalysisError("检索超时，请检查 Embedding 模型服务是否可用") from exc

    def cancel(self, principal, request_id):
        active = self.active.get(request_id)
        if active is None or active[0] != principal.name:
            raise AnalysisError("请求不存在或不属于当前用户")
        active[1].set()

    async def analyze(
        self,
        principal,
        source,
        question="",
        sql=None,
        approval_id=None,
        skill_name="general",
        explain=True,
    ):
        # 1. 权限与 Skill：用户只能选择已授权的数据源和已登记的分析方法。
        policy = self.settings.authorize(principal, source)
        skill = get_skill(skill_name)
        if approval_id and not sql:
            raise AnalysisError("确认执行必须携带先前返回的 SQL，禁止重新生成")
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=2)
        except asyncio.TimeoutError as exc:
            raise AnalysisError("分析任务繁忙，请稍后重试") from exc
        request_id = uuid.uuid4().hex
        cancelled = threading.Event()
        self.active[request_id] = (principal.name, cancelled)
        digest = ""
        try:
            yield {"event": "started", "data": {"request_id": request_id}}
            # 2. 有 SQL 就直接检查；没有 SQL 才先检索，再交给模型生成。
            if sql is None:
                yield {"event": "stage", "data": {"name": "retrieval"}}
                evidence = await self.context(principal, source, question)
                yield {"event": "context", "data": [asdict(doc) for doc in evidence]}
                yield {"event": "stage", "data": {"name": "generation"}}
                sql, clarification = await asyncio.wait_for(
                    self.model.generate_sql(question, evidence, policy, skill),
                    timeout=60,
                )
                if clarification:
                    yield {"event": "clarification", "data": {"message": clarification}}
                    return
            if cancelled.is_set():
                raise AnalysisError("查询已取消")

            yield {"event": "stage", "data": {"name": "validation"}}
            # 3. 用完整作用域检查真实表与字段，不能只检查最外层 SELECT。
            query = validate_sql(sql, policy)
            digest = query.digest
            executor = self.executors[source]
            yield {"event": "sql", "data": {"sql": query.sql}}
            yield {"event": "stage", "data": {"name": "cost_check"}}
            cost = await asyncio.to_thread(executor.assess, query.sql, cancelled)
            # 4. 确认请求必须重新校验和规划，不能拿旧编号执行一条新 SQL。
            if approval_id:
                # This request has already revalidated and re-planned the SQL.
                self.audit.consume(approval_id, principal, policy, query)
            elif cost.needs_confirmation:
                token = self.audit.issue(principal, policy, query)
                self.audit.record(
                    request_id, principal, source, digest, "awaiting_confirmation"
                )
                yield {
                    "event": "confirmation_required",
                    "data": {
                        "approval_id": token,
                        "expires_in": 300,
                        "sql": query.sql,
                        "cost": asdict(cost),
                    },
                }
                return  # No transaction/connection is held while waiting for a user.

            yield {"event": "stage", "data": {"name": "execution"}}
            # 5. 数据库还有独立的只读、超时和连接池限制。
            raw = await asyncio.to_thread(executor.query, query.sql, cancelled)
            if cancelled.is_set():
                raise AnalysisError("查询已取消")
            result = sanitize(raw, policy)
            # 6. 所有后续输出共享这一份安全结果，不再把 raw 交给模型。
            self.audit.record(
                request_id, principal, source, digest, "completed", result
            )
            # Safe data is emitted before optional explanation, so model errors
            # cannot turn a completed database query into an indefinite spinner.
            yield {
                "event": "result",
                "data": {
                    **asdict(result),
                    "sql": query.sql,
                    "chart": chart_spec(result),
                },
            }
            if explain:
                yield {"event": "stage", "data": {"name": "explanation"}}
                try:
                    text = await asyncio.wait_for(
                        self.model.explain(question, query.sql, result, skill),
                        timeout=60,
                    )
                    yield {"event": "explanation", "data": {"text": text}}
                except Exception:
                    yield {
                        "event": "warning",
                        "data": {"message": "查询已完成，模型解释暂不可用"},
                    }
        except asyncio.CancelledError:
            cancelled.set()
            self.audit.record(request_id, principal, source, digest, "cancelled")
            raise
        except Exception as exc:
            self.audit.record(request_id, principal, source, digest, "failed")
            message = (
                str(exc)
                if isinstance(exc, AnalysisError)
                else "分析失败，请检查服务配置"
            )
            yield {"event": "error", "data": {"message": message}}
        finally:
            cancelled.set()
            self.active.pop(request_id, None)
            self.slots.release()

    def close(self):
        for executor in self.executors.values():
            executor.close()
        for retriever in self.retrievers.values():
            retriever.close()
