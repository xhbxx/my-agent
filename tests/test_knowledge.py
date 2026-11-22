from concurrent.futures import ThreadPoolExecutor

import pytest

from app.knowledge.documents import Passage, TokenBudget, parse_document
from app.knowledge.store import KnowledgeStore
from app.knowledge.retrieval import retrieve
from app.knowledge.decision import retrieve_with_decision
from app.knowledge.citations import verify_answer
from app.knowledge.memory_policy import MemoryPolicy, importance, decayed_importance


def passage(text="甲概念的完整文献证据。", source="a.md", chunk="a"):
    return Passage(
        text, {"source": source, "chunk_id": chunk, "doc_version": "v1", "chapter": "第一章"}
    )


def publish(store, text="甲概念的完整文献证据。", version="v1", source="a.md"):
    task = store.submit(source, version, [passage(text, source)])
    store.process(task)
    return task


def test_rate_limit_retries_current_batch_without_losing_progress(store, monkeypatch):
    from app.knowledge import store as store_module

    original = store.embeddings.embed_documents
    calls = []
    waits = []

    class RateLimited(RuntimeError):
        status_code = 429

    def embed(texts):
        calls.append(list(texts))
        if len(calls) == 1:
            raise RateLimited("Token rate limit exceeded")
        return original(texts)

    monkeypatch.setattr(store.embeddings, "embed_documents", embed)
    monkeypatch.setattr(store_module.time, "sleep", waits.append)
    task = publish(store)
    assert store.task(task)["status"] == "ready"
    assert calls[0] == calls[1]
    assert waits == [61.0]


@pytest.mark.parametrize(
    "text", ["甲" * 500, "第一句。第二句！" * 80, "abc def\n" * 100, "甲乙丙🙂" * 100]
)
def test_chunks_preserve_text_and_budget(text):
    budget = TokenBudget(64)
    pieces = list(budget.split(text))
    assert "".join(text for _, _, text in pieces) == text
    assert all(budget.count(chunk) <= 64 for _, _, chunk in pieces)


@pytest.mark.parametrize(
    "suffix,content",
    [
        (".md", "# 原书\n## 原章\n正文内容足够长。"),
        (".json", '[{"text":"第一条文献正文。"},{"content":"第二条文献正文。"}]'),
        (".txt", "文献原始正文内容。"),
    ],
)
def test_parsing_and_location(tmp_path, suffix, content):
    path = tmp_path / f"book{suffix}"
    path.write_text(content, encoding="utf-8")
    version, chunks = parse_document(path)
    assert version and chunks
    assert all(
        c.metadata["doc_version"] == version and c.metadata["source"] == path.name for c in chunks
    )


def test_invalid_json_is_not_silently_indexed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('[{"title":"没有正文"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        parse_document(path)


def test_idempotent_task(store):
    first = publish(store)
    assert store.submit("a.md", "v1", [passage()]) == first
    assert store.process(first) == "ready"
    assert len(store.snapshot()[0]) == 1
    assert store.task(first)["attempts"] == 1


def test_new_version_replaces_only_its_document(store):
    publish(store)
    publish(store, "另一份文献内容。", source="b.md")
    publish(store, "甲文献更新后的内容。", version="v2")
    passages, _, index = store.snapshot()
    assert len(passages) == index.ntotal == 2
    assert {p.page_content for p in passages} == {"另一份文献内容。", "甲文献更新后的内容。"}


def test_failed_embedding_preserves_active_snapshot(store, monkeypatch):
    publish(store)
    store.snapshot()
    previous = store._loaded_version
    task = store.submit("a.md", "v2", [passage("更新后文献内容。")])

    def fail(_):
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(store.embeddings, "embed_documents", fail)
    with pytest.raises(RuntimeError):
        store.process(task)
    assert store.task(task)["status"] == "failed"
    assert store.snapshot()[0][0].page_content == "甲概念的完整文献证据。"
    assert store._loaded_version == previous


def test_retry_recovers_failed_task(store, monkeypatch):
    task = store.submit("a.md", "v1", [passage()])
    real = store.embeddings.embed_documents
    monkeypatch.setattr(store.embeddings, "embed_documents", lambda texts: [[float("nan")] * 32])
    with pytest.raises(ValueError):
        store.process(task)
    monkeypatch.setattr(store.embeddings, "embed_documents", real)
    assert store.retry_failed()[task] == "ready"


def test_outdated_task_cannot_replace_latest_submission(store):
    old = store.submit("a.md", "v1", [passage()])
    new = store.submit("a.md", "v2", [passage("最新版本的文献内容。")])
    assert store.process(old) == "superseded"
    assert store.process(new) == "ready"


def test_revert_to_previously_published_version(store):
    publish(store)
    publish(store, "第二版的文献内容。", "v2")
    publish(store)
    assert store.snapshot()[0][0].page_content == "甲概念的完整文献证据。"


def test_concurrent_workers_build_once(store, embeddings):
    task = store.submit("a.md", "v1", [passage()])
    second = KnowledgeStore(store.root, embeddings, "test-hash")
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(lambda s: s.process(task), [store, second])) == ["ready", "ready"]
    assert store.task(task)["attempts"] == 1


def test_reader_reloads_published_snapshot(store, embeddings):
    publish(store)
    reader = KnowledgeStore(store.root, embeddings, "test-hash")
    assert reader.snapshot()[0][0].page_content == "甲概念的完整文献证据。"
    publish(store, "新的完整证据内容。", "v2")
    assert reader.snapshot()[0][0].page_content == "新的完整证据内容。"


def test_actual_hnsw_and_hybrid_retrieval(store):
    publish(store)
    hits = retrieve(store, "甲概念", reranker=lambda q, docs, top_n: [(0, 0.9)])
    assert hits[0][0].metadata["chunk_id"] == "a"
    assert "HNSW" in type(store.snapshot()[2]).__name__


def test_missing_evidence_triggers_another_search():
    queries = []

    def search(store, query, *args):
        queries.append(query)
        return [(passage(), 0.9)]

    decisions = iter(
        [
            {
                "sufficient": False,
                "supported_ids": ["1"],
                "missing": ["乙的条件"],
                "next_query": "乙的条件",
            },
            {"sufficient": True, "supported_ids": ["1"], "missing": [], "next_query": ""},
        ]
    )
    result = retrieve_with_decision(
        None, "比较甲乙", judge=lambda p: next(decisions), search=search
    )
    assert queries == ["比较甲乙", "乙的条件"]
    assert not result.low_confidence


def test_repeated_queries_stop():
    result = retrieve_with_decision(
        None,
        "甲",
        judge=lambda p: {
            "sufficient": False,
            "supported_ids": [],
            "missing": ["条件"],
            "next_query": "甲",
        },
        search=lambda *args: [(passage(), 0.9)],
    )
    assert len(result.attempts) == 1 and result.low_confidence


def test_unknown_evidence_id_never_passes():
    result = retrieve_with_decision(
        None,
        "甲",
        judge=lambda p: {
            "sufficient": True,
            "supported_ids": ["99"],
            "missing": [],
            "next_query": "",
        },
        search=lambda *args: [(passage(), 0.9)],
    )
    assert result.low_confidence


@pytest.mark.parametrize("answer", ["无引用的结论", "结论[99]", "结论[0]"])
def test_invalid_citation_rejected(answer):
    assert not verify_answer(answer, [(passage(), 0.9)])["supported"]


def test_citation_support_is_checked():
    assert not verify_answer("错误结论[1]", [(passage(), 0.9)], lambda p: {"supported": False})[
        "supported"
    ]
    assert verify_answer("文献结论[1]", [(passage(), 0.9)], lambda p: {"supported": True})[
        "supported"
    ]


def test_entity_weights_and_round_decay():
    assert importance("有高血压并使用二甲双胍") > importance("喜欢看书")
    assert decayed_importance(0.8, 20) == pytest.approx(0.4)


def test_memory_conflict_is_quarantined(embeddings):
    policy = MemoryPolicy(embeddings, nli=lambda a, b: "contradiction", min_similarity=-1)
    admission = policy.admit("我没有高血压", [{"id": 1, "text": "我有高血压"}])
    assert admission.state == "conflict" and admission.conflicts == [1]


def test_memory_duplicate_and_neutral(embeddings):
    existing = [{"id": 1, "text": "我有高血压"}]
    assert (
        MemoryPolicy(embeddings, lambda a, b: "entailment", min_similarity=-1)
        .admit("我有高血压", existing)
        .state
        == "duplicate"
    )
    assert (
        MemoryPolicy(embeddings, lambda a, b: "neutral", min_similarity=-1)
        .admit("我有糖尿病", existing)
        .state
        == "active"
    )


def test_storage_scope_and_conflict(chat_store):
    a = chat_store.add_memory("我有高血压", conv_id=1)
    b = chat_store.add_memory("我有高血压", conv_id=2)
    assert a["id"] != b["id"]
    chat_store.add_memory("我没有高血压", conv_id=1, memory_state="conflict", conflicts=[a["id"]])
    assert all(
        m["memory_state"] == "conflict"
        for m in chat_store.list_memories(conv_id=1, scope="session")
    )
    assert chat_store.list_memories(conv_id=2, scope="session")[0]["memory_state"] == "active"


def test_aged_and_conflicting_memory_not_injected(embeddings):
    policy = MemoryPolicy(embeddings, min_similarity=-1)
    memories = [
        {"id": 1, "text": "我有高血压", "importance": 0.8, "turn_index": 0},
        {"id": 2, "text": "我有糖尿病", "memory_state": "conflict"},
    ]
    assert policy.select("我的情况", memories, 100) == []


def test_index_write_failure_keeps_previous_version(store, monkeypatch):
    import app.knowledge.store as module

    publish(store)
    old = store.snapshot()[0][0].page_content
    task = store.submit("a.md", "v2", [passage("新证据写入失败的测试。")])

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(module.np, "save", fail)
    with pytest.raises(OSError):
        store.process(task)
    assert store.snapshot()[0][0].page_content == old
    assert store.task(task)["status"] == "failed"


def test_other_source_is_retained_during_failed_update(store, monkeypatch):
    publish(store)
    publish(store, "另一本文献的原始内容。", source="b.md")
    task = store.submit("a.md", "v2", [passage("新版本内容。")])
    monkeypatch.setattr(store.embeddings, "embed_documents", lambda texts: [[0.0] * 32])
    with pytest.raises(ValueError):
        store.process(task)
    assert {p.metadata["source"] for p in store.snapshot()[0]} == {"a.md", "b.md"}
