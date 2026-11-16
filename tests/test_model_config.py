"""三个模型独立配置和向量空间切换，使用测试接口替身。"""

import json

import pytest


def test_three_clients_receive_independent_configuration(tmp_path, monkeypatch):
    from app import config, embed, llm, rerank

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".env")
    updates = {}
    for name in config.SERVICES:
        updates.update(
            {
                f"{name.upper()}_BASE_URL": f"https://{name}.example/v1",
                f"{name.upper()}_MODEL": f"custom-{name}",
                f"{name.upper()}_API_KEY": f"test-{name}-key",
            }
        )
    config.save_keys(updates)
    captured = {}
    monkeypatch.setattr(llm, "_llm", None)
    monkeypatch.setattr(embed, "_embeddings", None)
    monkeypatch.setattr(llm, "ChatOpenAI", lambda **kwargs: captured.setdefault("llm", kwargs))
    monkeypatch.setattr(
        embed, "OpenAIEmbeddings", lambda **kwargs: captured.setdefault("embedding", kwargs)
    )
    assert llm.get_llm()["api_key"] == "test-llm-key"
    assert embed.get_embeddings()["base_url"] == "https://embedding.example/v1"
    assert captured["llm"]["model"] == "custom-llm"
    assert captured["embedding"]["model"] == "custom-embedding"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"results":[{"index":0,"relevance_score":0.9}]}'

    def send(request, timeout):
        assert request.full_url == "https://rerank.example/v1/rerank"
        assert request.get_header("Authorization") == "Bearer test-rerank-key"
        assert json.loads(request.data)["model"] == "custom-rerank"
        return Response()

    monkeypatch.setattr(rerank.urllib.request, "urlopen", send)
    assert rerank.rerank("问题", ["证据"]) == [(0, 0.9)]


def test_endpoint_normalization_and_key_changes_do_not_change_vector_identity(
    tmp_path, monkeypatch
):
    from app import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".env")
    config.save_keys(
        {
            "EMBEDDING_BASE_URL": "https://vectors.example/v1/embeddings",
            "EMBEDDING_MODEL": "custom-vectors",
            "EMBEDDING_API_KEY": "test-one",
        }
    )
    identity = config.embedding_identity()
    assert config.service_settings("embedding")["base_url"] == "https://vectors.example/v1"
    config.save_keys({"EMBEDDING_API_KEY": "test-two"})
    assert config.embedding_identity() == identity
    config.save_keys({"EMBEDDING_MODEL": "different-model"})
    assert config.embedding_identity() != identity


def test_different_vector_model_reembeds_all_retained_documents(store):
    from app.knowledge.documents import Passage
    from app.knowledge.store import KnowledgeStore

    for source in ("a.md", "b.md"):
        task = store.submit(
            source, "v1", [Passage(source + "的文献正文", {"source": source, "chunk_id": source})]
        )
        store.process(task)

    class DifferentEmbeddings:
        def embed_documents(self, texts):
            return [[len(text), 1] for text in texts]

        def embed_query(self, text):
            return [len(text), 1]

    changed = KnowledgeStore(store.root, DifferentEmbeddings(), embedding_id="different-model")
    with pytest.raises(ValueError, match="重新建立"):
        changed.snapshot()
    task = changed.submit(
        "a.md", "v1", [Passage("a.md的新文献正文", {"source": "a.md", "chunk_id": "new-a"})]
    )
    changed.process(task)
    passages, vectors, index = changed.snapshot()
    assert {p.metadata["source"] for p in passages} == {"a.md", "b.md"}
    assert vectors.shape == (2, 2)
    assert index.d == 2
    assert changed.similarity_search("文献")
