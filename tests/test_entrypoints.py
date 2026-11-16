"""通过原有问答入口验证接线，模型使用明确的测试替身。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.knowledge.documents import Passage


class ScriptedModel:
    def invoke(self, prompt):
        text = str(prompt)
        if "证据审核器" in text:
            return SimpleNamespace(
                content='{"sufficient":true,"supported_ids":["1"],"missing":[],"next_query":""}'
            )
        if "逐条检查回答" in text:
            return SimpleNamespace(content='{"supported":true,"reason":"测试替身确认"}')
        if "从下面这轮对话" in text:
            return SimpleNamespace(content="无")
        if "改写成一个不依赖上下文" in text:
            return SimpleNamespace(content="甲概念的定义")
        return SimpleNamespace(content="文献将甲概念定义为测试条件A[1]。")

    def stream(self, prompt):
        yield self.invoke(prompt)


def configure(monkeypatch, store, embeddings):
    import app.rag as rag
    import app.knowledge.runtime as runtime
    import app.llm as llm
    import app.embed as embed
    import app.rerank as rerank

    model = ScriptedModel()
    monkeypatch.setattr(runtime, "_store", store)
    monkeypatch.setattr(rag, "get_llm", lambda: model)
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    monkeypatch.setattr(embed, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(
        rerank, "rerank", lambda q, docs, top_n: [(i, 0.95) for i in range(min(len(docs), top_n))]
    )
    task = store.submit(
        "test.md",
        "v1",
        [
            Passage(
                "甲概念的定义为测试条件A。",
                {
                    "source": "test.md",
                    "chunk_id": "one",
                    "chapter": "定义",
                    "page": 1,
                    "doc_version": "v1",
                },
            )
        ],
    )
    store.process(task)


def test_cli_session_uses_new_knowledge_chain(store, embeddings, chat_store, monkeypatch):
    from app.rag import RAGSession

    configure(monkeypatch, store, embeddings)
    result = RAGSession(auto_remember=False).ask("资料中甲概念如何定义？")
    assert result.citation_check["supported"]
    assert result.retrieved_contexts == ["甲概念的定义为测试条件A。"]
    assert result.sources[0]["chunk_id"] == "one"
    assert result.sources[0]["page"] == 1
    assert result.reflection[0]["accepted"]
    assert result.reflection[0]["action"] == "answer"
    assert result.reflection[0]["missing"] == []


def test_legacy_eval_and_agent_search_keep_provenance(store, embeddings, chat_store, monkeypatch):
    from app.eval import validate
    from app.agent.tools import ToolBox
    from app.agent.state import ConsultState

    configure(monkeypatch, store, embeddings)
    assert validate(
        [{"id": "one", "question": "甲", "gold_sources": ["test.md"], "gold_keywords": ["定义"]}]
    )
    box = ToolBox(ConsultState(), conv_id=1)
    result = box._t_search_knowledge({"query": "甲概念定义"})
    assert result["hits"][0]["chunk_id"] == "one"
    assert box.sources[0]["page"] == 1
    assert result["rounds"][0]["action"] == "answer"


def test_web_chat_sse_and_persistence(store, embeddings, chat_store, monkeypatch):
    from app.server import app

    configure(monkeypatch, store, embeddings)
    with TestClient(app) as client:
        response = client.post("/api/conversations", json={"title": "核心链路测试"})
        conv_id = response.json()["id"]
        reply = client.post(
            f"/api/conversations/{conv_id}/chat", json={"message": "资料中甲概念如何定义？"}
        )
        assert reply.status_code == 200
        assert '"type": "done"' in reply.text or '"type":"done"' in reply.text
        assert "citation_check" in reply.text
        history = client.get(f"/api/conversations/{conv_id}/messages").json()
        assert history[-1]["role"] == "assistant"
        assert history[-1]["sources"][0]["chunk_id"] == "one"


@pytest.mark.parametrize("supported", [True, False])
def test_sync_and_stream_share_validation_and_history(
    store, embeddings, chat_store, monkeypatch, supported
):
    from app.rag import RAGSession
    from app.knowledge import citations

    configure(monkeypatch, store, embeddings)
    checks = []

    def verify(answer, hits):
        checks.append(answer)
        return {"supported": supported, "reason": "共同核查入口"}

    monkeypatch.setattr(citations, "verify_answer", verify)
    sync_session = RAGSession(auto_remember=False)
    stream_session = RAGSession(auto_remember=False)
    result = sync_session.ask("资料中甲概念如何定义？")
    events = list(stream_session.ask_stream("资料中甲概念如何定义？"))
    done = next(event for event in events if event["type"] == "done")
    assert done["answer"] == result.answer
    assert sync_session.history == stream_session.history
    assert len(checks) == 2  # 每个入口只核查一次。
    assert (
        next(event for event in events if event["type"] == "sources")["sources"] == result.sources
    )
    check_index = next(i for i, event in enumerate(events) if event["type"] == "citation_check")
    first_delta = next(i for i, event in enumerate(events) if event["type"] == "delta")
    assert check_index < first_delta
    if not supported:
        assert "当前资料不足" in result.answer


@pytest.mark.parametrize("streaming", [False, True])
def test_memory_maintenance_runs_once(store, embeddings, chat_store, monkeypatch, streaming):
    from app.rag import RAGSession

    configure(monkeypatch, store, embeddings)
    conversation = chat_store.create_conversation("维护次数")
    session = RAGSession(conv_id=conversation["id"])
    calls = []
    monkeypatch.setattr(session.memory, "remember_turn", lambda *args: calls.append("remember"))
    monkeypatch.setattr(session.memory, "maybe_compress", lambda *args: calls.append("compress"))
    if streaming:
        list(session.ask_stream("资料中甲概念如何定义？"))
    else:
        session.ask("资料中甲概念如何定义？")
    assert calls == ["remember", "compress"]


def test_mcp_manifest_uses_published_snapshot(store, embeddings, chat_store, monkeypatch):
    import json
    from app import paths
    from app.mcp.resources import kb_manifest

    configure(monkeypatch, store, embeddings)
    monkeypatch.setattr(paths, "STORE_DIR", store.root)
    manifest = json.loads(kb_manifest())
    assert manifest["ok"]
    assert manifest["pipeline"] == "faiss-hnsw"
    assert manifest["embedding_model"] == "test-hash"
    assert manifest["total_chunks"] == 1
    assert manifest["corpus"][0]["name"] == "test.md"


def test_mcp_manifest_does_not_create_missing_store(tmp_path, monkeypatch):
    import json
    from app import paths
    from app.mcp.resources import kb_manifest

    root = tmp_path / "missing-knowledge"
    monkeypatch.setattr(paths, "STORE_DIR", root)
    assert json.loads(kb_manifest())["ok"] is False
    assert not root.exists()


def test_mcp_search_does_not_enter_generation_pipeline(store, embeddings, chat_store, monkeypatch):
    from app.mcp.tools import tcm_search
    from app.knowledge import decision

    configure(monkeypatch, store, embeddings)

    def unexpected_generation(*args):
        raise AssertionError("纯检索不应调用生成模型")

    monkeypatch.setattr(decision, "ask_json", unexpected_generation)
    result = tcm_search("甲概念定义")
    assert result["ok"]
    assert result["results"][0]["chunk_id"] == "one"
    assert result["reflection"]["attempts"] == 0


def test_old_memory_schema_migrates(chat_store):
    import sqlite3

    with sqlite3.connect(chat_store.DB_PATH) as db:
        db.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY,conv_id INTEGER,kind TEXT,text TEXT,created_at REAL)"
        )
        db.execute("INSERT INTO memories VALUES (1,7,'fact','旧记忆',0)")
    rows = chat_store.list_memories(conv_id=7, scope="session")
    assert rows[0]["memory_state"] == "active"
    assert rows[0]["importance"] == 0.5
    chat_store.add_memory("新的记忆", conv_id=7)


def test_pdf_scanned_page_uses_ocr(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    from app.knowledge.documents import parse_document
    from app.knowledge import ocr

    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as stream:
        writer.write(stream)
    monkeypatch.setattr(ocr, "read_page", lambda p, i: "扫描页中的可定位原文证据。")
    _, passages = parse_document(path)
    assert passages[0].metadata["page"] == 1
    assert "扫描页" in passages[0].page_content
