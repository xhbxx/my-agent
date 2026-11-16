"""调用设置中的重排接口，采用兼容Cohere的results/index/relevance_score格式。"""

import json
import urllib.request
from app.config import service_settings


def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """对候选文档按与 query 的相关度重排。

    Args:
        query:     查询文本（建议用"改写后的独立问题"效果更好）
        documents: 候选文档原文列表
        top_n:     只保留前 N 条，None 表示全部返回
    Returns:
        [(原始 documents 中的下标, relevance_score), ...] 按分数降序。
        score 是 0~1 的相关度（不是 logits，无需 sigmoid）。
    """
    if not documents:
        return []
    settings = service_settings("rerank")
    payload = {
        "model": settings["model"],
        "query": query,
        "documents": documents,
        "top_n": top_n or len(documents),
        "return_documents": False,  # 不回传原文，省带宽
    }
    req = urllib.request.Request(
        settings["base_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        results = json.loads(resp.read()).get("results", [])
    # results 已按 relevance_score 降序；index 指向原始 documents 下标
    return [(r["index"], r["relevance_score"]) for r in results]
