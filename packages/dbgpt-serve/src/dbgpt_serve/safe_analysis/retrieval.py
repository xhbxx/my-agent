"""Authorized schema/metrics -> BM25 + optional DB-GPT vector store -> RRF.

The index is built ONLY from this source policy. Never search a global index
then trust its metadata to authorize tables. SQL authorization stays in guard.py.
"""

import hashlib
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from rank_bm25 import BM25Okapi

from .contracts import AnalysisError
from .guard import visible_schema
from .knowledge import load_knowledge
from .retrieval_models import RemoteEmbeddings, rerank

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
DOCUMENT_TYPES = {".md", ".txt", ".pdf", ".docx"}


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
    def __init__(
        self,
        policy,
        vector_factory=None,
        owns_store=False,
        *,
        model_settings=None,
        knowledge_path=None,
    ):
        self.policy = policy
        self.knowledge_path = knowledge_path
        self.model_settings = model_settings or {}
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
        self._default_doc_count = len(self.documents)
        for path in self.document_paths()[len(policy.knowledge_files) :]:
            self._append_upload(Path(path).name, load_knowledge([path]))
        self._rebuild_bm25()
        self.vector_factory = vector_factory
        self._store = None
        self._store_version = None
        self._query_lock = threading.RLock()
        self.owns_store = owns_store

    def _rebuild_bm25(self):
        if len(self.documents) == self._default_doc_count:
            self._upload_version = ""
        else:
            identity = "".join(
                doc.id + hashlib.sha256(doc.content.encode()).hexdigest()
                for doc in sorted(
                    self.documents[self._default_doc_count :], key=lambda doc: doc.id
                )
            )
            self._upload_version = hashlib.sha256(identity.encode()).hexdigest()
        self.lookup = {doc.id: doc for doc in self.documents}
        corpus = [tokens(doc.content) or ["_"] for doc in self.documents]
        # BM25Okapi IDF can be negative for tiny corpora. A positive epsilon
        # floor keeps matching documents ahead of documents with no overlap.
        self.bm25 = BM25Okapi(corpus)
        self.bm25.idf = {
            term: max(score, 0.01) for term, score in self.bm25.idf.items()
        }

    def document_paths(self):
        uploaded = []
        if self.knowledge_path:
            uploaded = sorted(
                path
                for path in self.knowledge_path.glob("*")
                if path.is_file() and path.suffix.lower() in DOCUMENT_TYPES
            )
        return (*self.policy.knowledge_files, *uploaded)

    def add_document(self, filename, data):
        stem = Path(filename).stem.split(".")[0].upper()
        if (
            not re.fullmatch(r"[\w][\w .()\-]{0,119}", filename)
            or Path(filename).suffix.lower() not in DOCUMENT_TYPES
            or stem in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)
        ):
            raise AnalysisError("文件名无效，支持 PDF、DOCX、Markdown 和 TXT")
        if not 0 < len(data) <= MAX_DOCUMENT_BYTES:
            raise AnalysisError("文件不能为空，且不能超过 10 MB")
        path = self.knowledge_path
        if path is None:
            raise AnalysisError("此工作台未配置知识文档存储")
        with self._query_lock:
            existing = self.document_paths()
            if any(
                Path(item).name.casefold() == filename.casefold() for item in existing
            ):
                raise AnalysisError("同名文档已存在，请更换文件名")
            if len(existing) - len(self.policy.knowledge_files) >= 20:
                raise AnalysisError("每个数据源最多上传 20 个文档")
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=path) as stage:
                candidate = Path(stage) / filename
                candidate.write_bytes(data)
                try:
                    if candidate.suffix.lower() == ".docx":
                        with ZipFile(candidate) as archive:
                            if (
                                sum(item.file_size for item in archive.infolist())
                                > 40 * 1024 * 1024
                            ):
                                raise ValueError("expanded document too large")
                    chunks = list(load_knowledge([candidate]))
                    if not chunks:
                        raise ValueError("empty document")
                except Exception:
                    raise AnalysisError("文档无法解析或没有可检索的文字") from None
                candidate.replace(path / filename)
            self._append_upload(filename, chunks)
            self._rebuild_bm25()
            self.close()

    def _append_upload(self, filename, chunks):
        key = hashlib.sha256(filename.encode()).hexdigest()[:24]
        self.documents.extend(
            Evidence(f"upload:{key}:{index}", content, "document")
            for index, (_, content) in enumerate(chunks, start=1)
        )

    def _model(self, kind):
        store = self.model_settings.get(kind)
        return store.current if store else None

    def _vector_version(self, config):
        return (
            self.policy.name
            + self.policy.version
            + self._upload_version
            + (f"\n{config.base_url}\n{config.model}" if config else "")
        )

    @property
    def index_ready(self):
        return self._store is not None and self._store_version == self._vector_version(
            self._model("embedding")
        )

    def _vector_store(self):
        # Initialization and queries share _query_lock in retrieve().
        config = self._model("embedding")
        version = self._vector_version(config)
        if self._store is not None and self._store_version == version:
            return self._store
        self.close()
        if self.vector_factory is None:
            raise AnalysisError("已启用向量检索，但 DATA-AGENT 向量存储尚未初始化")
        from dbgpt.core import Chunk

        # Source + full policy fingerprint prevents stale cross-policy hits.
        name = "safe_" + hashlib.sha256(version.encode()).hexdigest()[:24]
        store = (
            self.vector_factory(name, RemoteEmbeddings(config))
            if config
            else self.vector_factory(name)
        )
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
        self._store_version = version
        return store

    def retrieve(self, question, top_k=6):
        with self._query_lock:
            return self._retrieve(question, top_k)

    def _retrieve(self, question, top_k):
        scores = self.bm25.get_scores(tokens(question))
        lexical = [
            self.documents[i].id
            for i in sorted(range(len(scores)), key=lambda i: -scores[i])
            if scores[i] > 0
        ][: top_k * 2]
        dense = []
        if self.policy.vector:
            try:
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
        ranking_model = self._model("rerank")
        limit = top_k * 2 if ranking_model else top_k
        ids = fuse_rankings(lexical, dense, top_k=limit)
        # A short/unknown question still needs authorized schema to generate SQL.
        if not ids:
            ids = [doc.id for doc in self.documents[:limit]]
        candidates = [self.lookup[doc_id] for doc_id in ids]
        if ranking_model and len(candidates) > 1:
            order = rerank(ranking_model, question, [doc.content for doc in candidates])
            candidates = [candidates[index] for index in order]
        return candidates[:top_k]

    def close(self):
        with self._query_lock:
            if self._store is not None and self.owns_store:
                self._store.close()
            self._store = None
            self._store_version = None
