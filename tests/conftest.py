import hashlib
import numpy as np
import pytest


class TestEmbeddings:
    """测试替身，只检查工程链路。不能用其结果报告领域检索效果。"""

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        vector = np.zeros(32, dtype="float32")
        for character in text:
            vector[hashlib.sha256(character.encode()).digest()[0] % 32] += 1
        return vector.tolist()


@pytest.fixture
def embeddings():
    return TestEmbeddings()


@pytest.fixture
def store(tmp_path, embeddings):
    from app.knowledge.store import KnowledgeStore

    return KnowledgeStore(tmp_path / "knowledge", embeddings, embedding_id="test-hash")


@pytest.fixture
def chat_store(tmp_path, monkeypatch):
    from app import storage

    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "chat.db")
    return storage
