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


def test_workbench_routes_and_safe_asset_paths():
    from dbgpt_app.safe_analysis_web.routes import DATABASE_ICONS, router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200 and "工作台令牌" not in page.text
        assert page.text.count("data-preset=") == 3
        assert "销售查询示例" in page.text
        assert "DATA-AGENT" in page.text and "DB-GPT" not in page.text
        assert client.get("/?classic=1").text == page.text
        assert client.get("/safe-analysis/").text == page.text
        assert 'id="view-sources"' in page.text
        assert 'id="view-knowledge"' in page.text
        assert "/construct/" not in page.text
        assert "Content-Security-Policy" in page.headers
        assert client.get("/safe-analysis/assets/app.js").status_code == 200
        assert client.get("/safe-analysis/assets/marked.umd.js").status_code == 200
        assert client.get("/safe-analysis/assets/favicon.svg").status_code == 200
        assert client.get("/safe-analysis/assets/not-allowed.txt").status_code == 404
        for kind in sorted(DATABASE_ICONS):
            icon = client.get(f"/safe-analysis/database-icons/{kind}")
            assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
        fallback = client.get("/safe-analysis/database-icons/unknown")
        assert fallback.status_code == 200
        assert fallback.headers["content-type"] == "image/svg+xml"
