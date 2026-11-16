"""本地评测入口。逐题记录检索结果、上下文与引用检查，按实际样本聚合。"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np


def load_cases(path):
    cases = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            raise ValueError(f"第{line_number}行缺少question")
        if not case.get("gold_sources") and not case.get("gold_chunk_ids"):
            raise ValueError(f"第{line_number}行缺少标准证据标注")
        cases.append(case)
    if not cases:
        raise ValueError("测试集为空")
    return cases


def retrieval_metrics(passages, case, k=30):
    by_chunk = bool(case.get("gold_chunk_ids"))
    expected = set(case.get("gold_chunk_ids") if by_chunk else case["gold_sources"])
    field = "chunk_id" if by_chunk else "source"
    actual = [p.metadata[field] for p in passages[:k]]
    return {
        "recall_at_30": len(expected.intersection(actual)) / len(expected),
        "hit_at_1": float(bool(actual) and actual[0] in expected),
        "label_level": "chunk" if by_chunk else "document",
    }


def index_latency(store, repeats=100):
    """仅计Faiss搜索，向量化、BM25、重排与生成耗时另行统计。"""
    _, vectors, index = store.snapshot()
    if index is None:
        raise ValueError("索引为空")
    samples = []
    for i in range(repeats):
        query = vectors[i % len(vectors) : i % len(vectors) + 1]
        start = time.perf_counter()
        index.search(query, min(30, len(vectors)))
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "index_p95_ms": float(np.percentile(samples, 95)),
        "index_vectors": len(vectors),
        "repeats": repeats,
        "threads": "faiss_default",
        "query_source": "indexed_vectors",
        "cache_state": "warm",
    }


def run_ragas(rows):
    from ragas import evaluate, EvaluationDataset, SingleTurnSample
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from app.llm import get_llm
    from app.embed import get_embeddings

    if any(not row.get("reference") for row in rows):
        raise ValueError("RAGAS上下文评测需要每题提供reference参考答案")
    samples = [
        SingleTurnSample(
            user_input=row["question"],
            response=row["answer"],
            retrieved_contexts=row["contexts"],
            reference=row["reference"],
        )
        for row in rows
    ]
    result = evaluate(
        EvaluationDataset(samples=samples),
        metrics=[Faithfulness(), LLMContextPrecisionWithReference(), LLMContextRecall()],
        llm=LangchainLLMWrapper(get_llm()),
        embeddings=LangchainEmbeddingsWrapper(get_embeddings()),
    )
    return json.loads(result.to_pandas().to_json(orient="records", force_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="评测本地文献问答")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("store/evaluation.json"))
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--retrieval-mode", choices=["dense", "hybrid"], default="hybrid")
    parser.add_argument("--no-reflection", action="store_true")
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    if args.validate_only:
        print(json.dumps({"valid_cases": len(cases)}, ensure_ascii=False))
        return
    from app.rag import RAGSession
    from .runtime import get_store
    from .retrieval import retrieve

    from app.config import service_settings

    store = get_store()
    rows = []
    for case in cases:
        start = time.perf_counter()
        if args.retrieval_mode == "dense":
            documents = store.similarity_search(case["question"], k=30)
        else:
            documents = [p for p, _ in retrieve(store, case["question"], 30, 30)]
        row = {
            "id": case.get("id"),
            "question": case["question"],
            "reference": case.get("reference", ""),
            **retrieval_metrics(documents, case),
            "retrieval_ms": (time.perf_counter() - start) * 1000,
        }
        if args.generate or args.ragas:
            result = RAGSession(auto_remember=False, reflect=not args.no_reflection).ask(
                case["question"]
            )
            row.update(
                answer=result.answer,
                contexts=result.retrieved_contexts,
                citation_check=result.citation_check,
                trace=result.reflection,
            )
        rows.append(row)
    report = {
        "dataset": str(args.dataset.resolve()),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "retrieval_mode": args.retrieval_mode,
        "models": {
            "embedding": store.embedding_id,
            "reranker": service_settings("rerank")["model"],
            "llm": service_settings("llm")["model"],
        },
        "reflection": not args.no_reflection,
        "sample_count": len(rows),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "snapshot": store._loaded_version,
        "metrics": {
            name: float(np.mean([r[name] for r in rows])) for name in ("recall_at_30", "hit_at_1")
        },
        "index_benchmark": index_latency(store),
        "cases": rows,
    }
    if args.ragas:
        report["ragas"] = run_ragas(rows)
    if args.baseline_report:
        baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
        if baseline.get("dataset_sha256") != report["dataset_sha256"]:
            raise ValueError("基线与当前报告必须使用同一份测试集")
        report["delta"] = {
            name: value - baseline["metrics"][name] for name, value in report["metrics"].items()
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
