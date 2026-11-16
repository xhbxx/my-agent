"""对标注的多轮记忆样本测召回与误用，使用实际配置的BGE。"""

import argparse
import hashlib
import json
from pathlib import Path

from .memory_policy import MemoryPolicy


def load_cases(path):
    cases = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError("测试集为空")
    for case in cases:
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError("记忆样本缺少query")
        if not isinstance(case.get("current_round"), int) or case["current_round"] < 0:
            raise ValueError("current_round必须为非负整数")
        ids = {memory["id"] for memory in case["memories"]}
        if not set(case["expected_ids"]).issubset(ids):
            raise ValueError("expected_ids含样本中不存在的记忆编号")
    return cases


def measure(cases, policy):
    rows = []
    for case in cases:
        expected = set(case["expected_ids"])
        selected = policy.select(case["query"], case["memories"], case["current_round"])
        actual = {memory["id"] for memory in selected}
        rows.append(
            {
                "id": case["id"],
                "expected_ids": sorted(expected),
                "actual_ids": sorted(actual),
                "recall": len(expected & actual) / len(expected) if expected else float(not actual),
                "misuse_count": len(actual - expected),
                "selected_count": len(actual),
            }
        )
    selected_count = sum(row["selected_count"] for row in rows)
    return {
        "sample_count": len(rows),
        "mean_memory_recall": sum(row["recall"] for row in rows) / len(rows),
        "memory_misuse_rate": sum(row["misuse_count"] for row in rows) / max(selected_count, 1),
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="评估分层记忆选择策略")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("store/memory-evaluation.json"))
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    if args.validate_only:
        print(json.dumps({"valid_cases": len(cases)}))
        return
    from app.embed import get_embeddings

    report = measure(cases, MemoryPolicy(get_embeddings()))
    report["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    from app.config import embedding_identity

    report["embedding_model"] = embedding_identity()
    if args.baseline_report:
        baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
        if baseline.get("dataset_sha256") != report["dataset_sha256"]:
            raise ValueError("基线与当前报告必须使用同一份测试集")
        report["delta"] = {
            name: report[name] - baseline[name]
            for name in ("mean_memory_recall", "memory_misuse_rate")
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
