import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_app.openapi.api_v1 import workbench_management_api as api
from dbgpt_app.openapi.api_v1.safe_analysis_api import get_principal
from dbgpt_serve.safe_analysis import retrieval_models
from dbgpt_serve.safe_analysis.contracts import AnalysisError, SourcePolicy
from dbgpt_serve.safe_analysis.management import ModelConnection, ModelSettings
from dbgpt_serve.safe_analysis.retrieval import ContextRetriever


def policy(vector=False):
    return SourcePolicy(
        name="sales",
        url="sqlite://",
        vector=vector,
        tables={"sales": {"amount": "INT", "region": "TEXT"}},
        metrics=["amount benchmark"],
    )


def config(model="test-model"):
    return ModelConnection(
        base_url="https://example.invalid/v1", model=model, api_key="secret"
    )


def test_document_upload_persists_and_is_source_scoped(tmp_path):
    retriever = ContextRetriever(policy(), knowledge_path=tmp_path / "sales")
    retriever.add_document(
        "销售口径.md", b"# Refunds\n\nRefundRate is defined by settled orders."
    )
    assert any(
        "RefundRate" in item.content for item in retriever.retrieve("RefundRate")
    )
    assert not any(
        item.kind == "document"
        for item in ContextRetriever(
            policy(), knowledge_path=tmp_path / "other"
        ).retrieve("RefundRate")
    )
    reopened = ContextRetriever(policy(), knowledge_path=tmp_path / "sales")
    assert reopened.retrieve("RefundRate") == retriever.retrieve("RefundRate")
    assert reopened._upload_version == retriever._upload_version
    for name in ("../secret.md", "secret.exe", "CON.txt", "销售口径.md"):
        with pytest.raises(AnalysisError):
            retriever.add_document(name, b"not written")


def test_upload_api_requires_manager_and_source_access(monkeypatch, tmp_path):
    token = "synthetic-admin-token-for-knowledge"
    monkeypatch.setenv("DATA_AGENT_ADMIN_TOKEN", token)
    app = FastAPI()
    app.include_router(api.router)
    retriever = ContextRetriever(policy(), knowledge_path=tmp_path)

    def authorize(_principal, source):
        if source != "sales":
            raise AnalysisError("not allowed")

    app.state.safe_analysis_service = SimpleNamespace(
        settings=SimpleNamespace(authorize=authorize),
        retrievers={"sales": retriever},
    )
    app.dependency_overrides[get_principal] = lambda: object()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
    }
    with TestClient(app) as client:
        path = "/v1/safe-analysis/manage/knowledge"
        params = {"source": "sales", "filename": "refunds.txt"}
        assert (
            client.post(path, params=params, content=b"RefundRate").status_code == 403
        )
        assert (
            client.post(
                path,
                params={**params, "source": "other"},
                headers=headers,
                content=b"RefundRate",
            ).status_code
            == 403
        )
        result = client.post(
            path, params=params, headers=headers, content=b"RefundRate"
        )
        assert result.json() == {"filename": "refunds.txt"}


def test_vector_model_versions_index_and_rerank_orders_candidates(monkeypatch):
    calls = []
    embedding = SimpleNamespace(current=None)
    ranking = SimpleNamespace(current=None)

    class Store:
        def load_document(self, chunks):
            self.chunks = chunks

        def similar_search(self, query, top_k):
            return self.chunks

        def close(self):
            calls.append("closed")

    def factory(name, adapter=None):
        calls.append((name, adapter.config.model if adapter else None))
        return Store()

    retriever = ContextRetriever(
        policy(vector=True),
        factory,
        owns_store=True,
        model_settings={"embedding": embedding, "rerank": ranking},
    )
    initial = retriever.retrieve("amount")
    embedding.current = config("new-vector-model")
    assert not retriever.index_ready
    assert retriever.retrieve("amount") == initial
    assert calls[1] == "closed" and calls[0][0] != calls[2][0]
    assert calls[2][1] == "new-vector-model"
    ranking.current = config("new-ranking-model")
    monkeypatch.setattr("dbgpt_serve.safe_analysis.retrieval.rerank", lambda *_: [1, 0])
    assert retriever.retrieve("amount") == initial[::-1]


@pytest.mark.parametrize("with_suffix", [False, True])
@pytest.mark.parametrize(
    ("base_url", "trust_env"),
    [("https://example.invalid/v1", True), ("http://127.0.0.1:11434/v1", False)],
)
def test_remote_embedding_and_rerank_shapes(
    monkeypatch, with_suffix, base_url, trust_env
):
    def respond(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path == "/v1/embeddings":
            assert body["input"] == ["one", "two"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [2.0]},
                        {"index": 0, "embedding": [1.0]},
                    ]
                },
            )
        assert request.url.path == "/v1/rerank"
        assert body["documents"] == ["one", "two"]
        return httpx.Response(200, json={"results": [{"index": 1}]})

    client = httpx.Client

    def mock_client(**kwargs):
        assert kwargs == {
            "timeout": 30,
            "follow_redirects": False,
            "trust_env": trust_env,
        }
        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(retrieval_models.httpx, "Client", mock_client)

    def configured(suffix):
        entry = config()
        entry.base_url = base_url
        if with_suffix:
            entry.base_url += f"/{suffix}"
        return entry

    assert retrieval_models.RemoteEmbeddings(configured("embeddings")).embed_documents(
        ["one", "two"]
    ) == [[1.0], [2.0]]
    assert retrieval_models.rerank(configured("rerank"), "query", ["one", "two"]) == [
        1,
        0,
    ]


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_retrieval_model_settings_persist_independently(monkeypatch, tmp_path, kind):
    token = "synthetic-admin-token-for-model-settings"
    monkeypatch.setenv("DATA_AGENT_ADMIN_TOKEN", token)
    app = FastAPI()
    app.include_router(api.router)
    store = ModelSettings(tmp_path / f"{kind}.json", kind=kind)
    app.state.safe_analysis_service = SimpleNamespace(
        qdrant_path=tmp_path,
        retrieval_models={kind: store},
    )
    probes = []
    monkeypatch.setattr(
        api,
        "probe_retrieval_model",
        lambda category, connection: probes.append(category),
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        path = f"/v1/safe-analysis/manage/model?kind={kind}"
        body = {
            "model": "model",
            "base_url": "https://example.invalid/v1/"
            + ("embeddings" if kind == "embedding" else "rerank"),
            "api_key": "secret-only",
        }
        assert client.post(
            path + "&test_only=true", json=body, headers=headers
        ).is_success
        assert not store.path.exists()
        assert client.post(path, json=body, headers=headers).json()["active"]
        assert probes == [kind, kind] and "secret-only" not in store.path.read_text()
        assert client.delete(path, headers=headers).is_success
        assert not store.path.exists()
