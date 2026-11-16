"""核对真实RAGAS引擎接线，评分模型为脚本替身，结果不用于效果报告。"""

import json

import pytest

from app.knowledge.evaluate import load_cases, retrieval_metrics
from app.knowledge.documents import Passage


def test_retrieval_metrics_use_relevant_set():
    actual = [Passage("正文", {"source": "a.md"}), Passage("正文2", {"source": "a.md"})]
    result = retrieval_metrics(actual, {"gold_sources": ["a.md", "b.md"]})
    assert result["recall_at_30"] == 0.5
    assert result["hit_at_1"] == 1


def test_dataset_requires_gold_evidence(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"question":"问题"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


def test_memory_metrics_count_missed_and_wrong_items():
    from app.knowledge.evaluate_memory import measure

    class FixedPolicy:
        def select(self, query, memories, current_round):
            return [{"id": 1}, {"id": 3}]

    report = measure(
        [
            {
                "id": "one",
                "query": "问题",
                "memories": [],
                "current_round": 1,
                "expected_ids": [1, 2],
            }
        ],
        FixedPolicy(),
    )
    assert report["mean_memory_recall"] == 0.5
    assert report["memory_misuse_rate"] == 0.5


def test_memory_dataset_rejects_unknown_labels(tmp_path):
    from app.knowledge.evaluate_memory import load_cases

    path = tmp_path / "memory.jsonl"
    path.write_text(
        json.dumps({"query": "问题", "current_round": 1, "memories": [], "expected_ids": [7]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="不存在"):
        load_cases(path)


def test_real_ragas_engine_with_scripted_judge(monkeypatch):
    pytest.importorskip("ragas")
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatResult, ChatGeneration
    from langchain_core.embeddings import FakeEmbeddings
    import app.llm
    import app.embed
    from app.knowledge.evaluate import run_ragas

    monkeypatch.setenv("RAGAS_DO_NOT_TRACK", "true")

    class Judge(BaseChatModel):
        @property
        def _llm_type(self):
            return "scripted-test-judge"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            prompt = str(messages)
            if "analyze the complexity" in prompt:
                output = {"statements": ["Alpha is A."]}
            elif "judge the faithfulness" in prompt:
                output = {
                    "statements": [
                        {"statement": "Alpha is A.", "reason": "Explicit text", "verdict": 1}
                    ]
                }
            elif "verify if the context was useful" in prompt:
                output = {"reason": "Explicit text", "verdict": 1}
            else:
                output = {
                    "classifications": [
                        {"statement": "Alpha is A.", "reason": "Explicit text", "attributed": 1}
                    ]
                }
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=json.dumps(output)))]
            )

    monkeypatch.setattr(app.llm, "get_llm", lambda: Judge())
    monkeypatch.setattr(app.embed, "get_embeddings", lambda: FakeEmbeddings(size=32))
    scores = run_ragas(
        [
            {
                "question": "What is alpha?",
                "answer": "Alpha is A.",
                "reference": "Alpha is A.",
                "contexts": ["Alpha is A."],
            }
        ]
    )
    assert len(scores) == 1
    assert scores[0]["faithfulness"] == pytest.approx(1)
    assert scores[0]["context_recall"] == pytest.approx(1)
