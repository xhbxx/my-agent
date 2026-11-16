"""Faiss快照和SQLite任务表。只有完整快照才能成为读者可见的版本。"""

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import faiss
from filelock import FileLock
import numpy as np

from .documents import Passage


class KnowledgeStore:
    def __init__(self, root, embeddings, embedding_id="BAAI/bge-m3"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.embeddings = embeddings
        self.embedding_id = embedding_id
        self._loaded_version = None
        self._loaded_embedding_id = None
        self._snapshot = None
        self._snapshot_lock = threading.Lock()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    source TEXT PRIMARY KEY, desired_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, version TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL, UNIQUE(source, version)
                );
                CREATE TABLE IF NOT EXISTS active (
                    id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / "catalog.db", timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, source, version, passages):
        version = hashlib.sha256(f"{version}:{self.embedding_id}".encode()).hexdigest()
        task_id = hashlib.sha256(f"{source}:{version}".encode()).hexdigest()
        payload = json.dumps([asdict(p) for p in passages], ensure_ascii=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT desired_version FROM documents WHERE source=?", (source,)
            ).fetchone()
            changed = previous is None or previous["desired_version"] != version
            db.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(source) DO UPDATE SET desired_version=excluded.desired_version",
                (source, version),
            )
            db.execute(
                "INSERT OR IGNORE INTO tasks(id,source,version,payload,updated) VALUES (?,?,?,?,?)",
                (task_id, source, version, payload, time.time()),
            )
            # 回退到曾经入库过的文档版本也必须重新发布。
            if changed:
                db.execute(
                    "UPDATE tasks SET status='pending',attempts=0,error='',updated=? WHERE id=?",
                    (time.time(), task_id),
                )
        return task_id

    def task(self, task_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def _read_snapshot(self, allow_model_mismatch=False):
        # Web会话共用store，快照与版本号必须作为同一份缓存发布。
        with self._snapshot_lock:
            return self._load_snapshot(allow_model_mismatch)

    def _load_snapshot(self, allow_model_mismatch=False):
        with self.connection() as db:
            row = db.execute("SELECT version FROM active WHERE id=1").fetchone()
        if not row:
            return [], np.empty((0, 0), dtype="float32"), None
        version = row["version"]
        if version != self._loaded_version:
            folder = self.root / "snapshots" / version
            manifest = json.loads((folder / "passages.json").read_text(encoding="utf-8"))
            passages = [Passage(**p) for p in manifest["passages"]]
            vectors = np.load(folder / "vectors.npy", allow_pickle=False)
            index = faiss.deserialize_index(
                np.frombuffer((folder / "index.faiss").read_bytes(), dtype="uint8")
            )
            if index.ntotal != len(passages) or len(vectors) != len(passages):
                raise ValueError("快照的索引与片段数量不一致")
            self._snapshot = passages, vectors, index
            self._loaded_version = version
            self._loaded_embedding_id = manifest["embedding_id"]
        if self._loaded_embedding_id != self.embedding_id and not allow_model_mismatch:
            raise ValueError("向量模型或API地址已更换，请在设置中重新建立知识库")
        return self._snapshot

    def process(self, task_id):
        # 文件锁由操作系统管理，进程退出自动释放。所有索引写者共用同一把锁。
        with FileLock(str(self.root / "index.lock"), timeout=30):
            task = self.task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task["status"] in {"ready", "superseded"}:
                return task["status"]
            with self.connection() as db:
                db.execute(
                    "UPDATE tasks SET status='building', attempts=attempts+1, updated=? WHERE id=?",
                    (time.time(), task_id),
                )
            try:
                return self._build(task)
            except Exception as exc:
                with self.connection() as db:
                    db.execute(
                        "UPDATE tasks SET status='failed',error=?,updated=? WHERE id=?",
                        (str(exc), time.time(), task_id),
                    )
                raise

    def _build(self, task):
        old_passages, old_vectors, _ = self._read_snapshot(allow_model_mismatch=True)
        retained = [i for i, p in enumerate(old_passages) if p.metadata["source"] != task["source"]]
        incoming = [Passage(**p) for p in json.loads(task["payload"])]
        if old_passages and self._loaded_embedding_id != self.embedding_id:
            # 更换向量空间时重算所有保留正文，不能拼接旧模型的向量。
            incoming = [old_passages[i] for i in retained] + incoming
            retained = []
        encoded = []
        for start in range(0, len(incoming), 32):
            batch = incoming[start : start + 32]
            encoded.extend(self.embeddings.embed_documents([p.page_content for p in batch]))
        new_vectors = np.asarray(encoded, dtype="float32")
        if (
            new_vectors.ndim != 2
            or len(new_vectors) != len(incoming)
            or not np.isfinite(new_vectors).all()
        ):
            raise ValueError("向量模型返回无效的片段向量")
        if np.any(np.linalg.norm(new_vectors, axis=1) == 0):
            raise ValueError("向量模型返回零向量")
        vectors = np.concatenate([old_vectors[retained], new_vectors]) if retained else new_vectors
        vectors = np.ascontiguousarray(vectors, dtype="float32")
        faiss.normalize_L2(vectors)
        index = faiss.IndexHNSWFlat(vectors.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = 100
        index.hnsw.efSearch = 64
        index.add(vectors)
        passages = [old_passages[i] for i in retained] + incoming
        snapshot_id = uuid.uuid4().hex
        folder = self.root / "snapshots" / snapshot_id
        folder.mkdir(parents=True)
        (folder / "index.faiss").write_bytes(faiss.serialize_index(index).tobytes())
        np.save(folder / "vectors.npy", vectors, allow_pickle=False)
        (folder / "passages.json").write_text(
            json.dumps(
                {
                    "embedding_id": self.embedding_id,
                    "passages": [asdict(p) for p in passages],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        for path in folder.iterdir():
            with path.open("r+b") as handle:
                os.fsync(handle.fileno())
        # 校验落盘文件后才发布。数据库提交同时更新可见指针和任务状态。
        check = faiss.deserialize_index(
            np.frombuffer((folder / "index.faiss").read_bytes(), dtype="uint8")
        )
        if check.ntotal != len(passages):
            raise ValueError("新快照校验失败")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            desired = db.execute(
                "SELECT desired_version FROM documents WHERE source=?", (task["source"],)
            ).fetchone()[0]
            if desired != task["version"]:
                db.execute(
                    "UPDATE tasks SET status='superseded',updated=? WHERE id=?",
                    (time.time(), task["id"]),
                )
                return "superseded"
            db.execute(
                "INSERT INTO active VALUES (1,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version",
                (snapshot_id,),
            )
            db.execute(
                "UPDATE tasks SET status='ready',error='',updated=? WHERE id=?",
                (time.time(), task["id"]),
            )
        return "ready"

    def retry_failed(self, max_attempts=3):
        with self.connection() as db:
            tasks = db.execute(
                "SELECT id FROM tasks WHERE status IN ('pending','failed','building') AND attempts < ? ORDER BY updated",
                (max_attempts,),
            ).fetchall()
        results = {}
        for task in tasks:
            try:
                results[task["id"]] = self.process(task["id"])
            except Exception as exc:
                results[task["id"]] = str(exc)
        return results

    def snapshot(self):
        return self._read_snapshot()

    def similarity_search(self, query, k=30, snapshot=None):
        passages, vectors, index = snapshot if snapshot is not None else self._read_snapshot()
        if index is None:
            raise RuntimeError("知识库为空，请先运行 python -m app.index")
        query_vector = np.asarray([self.embeddings.embed_query(query)], dtype="float32")
        if query_vector.shape[1] != vectors.shape[1] or not np.isfinite(query_vector).all():
            raise ValueError("查询向量维度或数值无效")
        if np.linalg.norm(query_vector) == 0:
            return []
        faiss.normalize_L2(query_vector)
        _, ids = index.search(query_vector, min(k, len(passages)))
        return [passages[i] for i in ids[0] if i >= 0]
