"""Authorized schema/metrics -> BM25 + optional DB-GPT vector store -> RRF.

The index is built ONLY from this source policy. Never search a global index
then trust its metadata to authorize tables. SQL authorization stays in guard.py.
"""

import hashlib
import re
import threading
import uuid
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from .contracts import AnalysisError
from .guard import visible_schema
from .knowledge import load_knowledge


def tokens(text):
    # Names/English words + Chinese characters and bigrams. No external tokenizer.
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return words + [part[i : i + 2] for part in chinese for i in range(len(part) - 1)]


@dataclass(frozen=True)
class Evidence:
    id: str
    content: str
    kind: str


def fuse_rankings(*rankings, k=60, top_k=6):
    scores = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))[:top_k]


class ContextRetriever:
    def __init__(self, policy, vector_factory=None, owns_store=False):
        self.policy = policy
        self.documents = []
        for name, columns in visible_schema(policy).items():
            text = f"表 {policy.schema}.{name}: " + ", ".join(
                f"{column} ({kind})" for column, kind in columns.items()
            )
            self.documents.append(Evidence("table:" + name, text, "schema"))
        for i, metric in enumerate(policy.metrics):
            self.documents.append(Evidence(f"metric:{i}", metric, "metric"))
        for doc_id, text in load_knowledge(policy.knowledge_files):
            self.documents.append(Evidence(doc_id, text, "document"))
        self.lookup = {doc.id: doc for doc in self.documents}
        corpus = [tokens(doc.content) or ["_"] for doc in self.documents]
        # BM25Okapi IDF can be negative for tiny corpora. A positive epsilon
        # floor keeps matching documents ahead of documents with no overlap.
        self.bm25 = BM25Okapi(corpus)
        self.bm25.idf = {
            term: max(score, 0.01) for term, score in self.bm25.idf.items()
        }
        self.vector_factory = vector_factory
        self._store = None
        self._query_lock = threading.Lock()
        self.owns_store = owns_store

    def _vector_store(self):
        # Initialization and queries share _query_lock in retrieve().
        if self._store is not None:
            return self._store
        if self.vector_factory is None:
            raise AnalysisError("已启用向量检索，但 DATA-AGENT 向量存储尚未初始化")
        from dbgpt.core import Chunk

        # Source + full policy fingerprint prevents stale cross-policy hits.
        name = (
            "safe_"
            + hashlib.sha256(
                (self.policy.name + self.policy.version).encode()
            ).hexdigest()[:24]
        )
        store = self.vector_factory(name)
        try:
            store.load_document(
                [
                    Chunk(
                        chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, name + doc.id)),
                        content=doc.content,
                        metadata={"evidence_id": doc.id},
                    )
                    for doc in self.documents
                ]
            )
        except Exception:
            if self.owns_store:
                store.close()
            raise
        self._store = store
        return store

    def retrieve(self, question, top_k=6):
        scores = self.bm25.get_scores(tokens(question))
        lexical = [
            self.documents[i].id
            for i in sorted(range(len(scores)), key=lambda i: -scores[i])
            if scores[i] > 0
        ][: top_k * 2]
        dense = []
        if self.policy.vector:
            try:
                with self._query_lock:
                    chunks = self._vector_store().similar_search(question, top_k * 2)
            except AnalysisError:
                raise
            except Exception as exc:
                raise AnalysisError(
                    "向量检索失败，请检查 Qdrant 配置与 Embedding 模型服务"
                ) from exc
            dense = [
                chunk.metadata.get("evidence_id")
                for chunk in chunks
                if chunk.metadata.get("evidence_id") in self.lookup
            ]
        ids = fuse_rankings(lexical, dense, top_k=top_k)
        # A short/unknown question still needs authorized schema to generate SQL.
        if not ids:
            ids = [doc.id for doc in self.documents[:top_k]]
        return [self.lookup[doc_id] for doc_id in ids]

    def close(self):
        if self.owns_store:
            with self._query_lock:
                if self._store is None:
                    return
                self._store.close()
                self._store = None
