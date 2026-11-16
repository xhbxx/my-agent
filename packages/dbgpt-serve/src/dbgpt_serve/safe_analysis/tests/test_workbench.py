"""Real local Qdrant persistence and browser asset integration."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_serve.safe_analysis.retrieval import ContextRetriever


class TestEmbeddings:
    """Deterministic test vectors: no model API is called."""

    def embed_query(self, text):
        return [
            float("amount" in text or "销售" in text),
            float("region" in text or "区域" in text),
            0.1,
        ]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


def test_real_qdrant_persists_and_reopens(tmp_path):
    from dbgpt.core import Chunk
    from dbgpt_ext.storage.vector_store.qdrant_store import (
        QdrantStore,
        QdrantVectorConfig,
    )

    config = QdrantVectorConfig(path=str(tmp_path / "qdrant"))
    store = QdrantStore(config, name="evidence", embedding_fn=TestEmbeddings())
    store.load_document(
        [
            Chunk(
                chunk_id="sales",
                content="amount 销售额",
                metadata={"evidence_id": "sales"},
            ),
            Chunk(
                chunk_id="regions",
                content="region 区域",
                metadata={"evidence_id": "regions"},
            ),
        ]
    )
    assert store.similar_search("销售", 1)[0].metadata["evidence_id"] == "sales"
    store.close()
    reopened = QdrantStore(config, name="evidence", embedding_fn=TestEmbeddings())
    try:
        assert (
            reopened.similar_search("区域", 1)[0].metadata["evidence_id"] == "regions"
        )
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_hybrid_retrieval_with_real_qdrant(tmp_path):
    import asyncio

    from dbgpt_ext.storage.vector_store.qdrant_store import (
        QdrantStore,
        QdrantVectorConfig,
    )
    from dbgpt_serve.safe_analysis.contracts import SourcePolicy

    policy = SourcePolicy(
        name="sales",
        url="sqlite://",
        vector=True,
        tables={"sales": {"amount": "REAL", "region": "TEXT"}},
        metrics=["销售额 = SUM(amount)"],
    )
    retriever = ContextRetriever(
        policy,
        lambda name: QdrantStore(
            QdrantVectorConfig(path=str(tmp_path / name)),
            name=name,
            embedding_fn=TestEmbeddings(),
        ),
        owns_store=True,
    )
    try:
        first, second = await asyncio.gather(
            asyncio.to_thread(retriever.retrieve, "销售额"),
            asyncio.to_thread(retriever.retrieve, "区域"),
        )
        assert any(item.kind == "metric" for item in first)
        assert any(item.kind == "schema" for item in second)
        retriever.close()
        reopened = await asyncio.to_thread(retriever.retrieve, "销售额")
        assert reopened == first
    finally:
        retriever.close()


def test_workbench_routes_and_safe_asset_paths(tmp_path, monkeypatch):
    from dbgpt_app.safe_analysis_web.routes import mount_home, router

    app = FastAPI()
    app.include_router(router)
    monkeypatch.setenv("DATA_AGENT_CONFIG", "enabled")
    (tmp_path / "index.html").write_text("Original DB-GPT", encoding="utf-8")
    mount_home(app, tmp_path)
    with TestClient(app) as client:
        assert (
            client.get("/", follow_redirects=False).headers["location"]
            == "/safe-analysis/"
        )
        assert client.get("/?classic=1").text == "Original DB-GPT"
        page = client.get("/safe-analysis/")
        assert page.status_code == 200 and "工作台令牌" in page.text
        assert "DATA-AGENT" in page.text and "DB-GPT" not in page.text
        assert "/logo_zh_latest.png" not in page.text
        assert "Content-Security-Policy" in page.headers
        assert client.get("/safe-analysis/assets/app.js").status_code == 200
        assert client.get("/safe-analysis/assets/marked.umd.js").status_code == 200
        assert client.get("/safe-analysis/assets/favicon.svg").status_code == 200
        assert client.get("/safe-analysis/assets/not-allowed.txt").status_code == 404
