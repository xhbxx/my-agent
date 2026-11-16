"""知识库实例入口，供入库、检索和评估共用。"""

from app.config import embedding_identity
from app.embed import get_embeddings
from app.paths import STORE_DIR

from .store import KnowledgeStore

_store = None


def get_store() -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore(STORE_DIR, get_embeddings(), embedding_id=embedding_identity())
    return _store
