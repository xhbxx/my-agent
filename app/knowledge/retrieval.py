"""稠密召回和BM25通过RRF融合，再做质量过滤与重排。"""

from collections import Counter
import math
import re


def terms(text):
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    # 中文双字词保留专业词的局部结构，英文使用完整词。
    return words + [a + b for a, b in zip(words, words[1:]) if len(a) == len(b) == 1]


def lexical_search(passages, query, k):
    tokenized = [terms(p.page_content) for p in passages]
    if not tokenized:
        return []
    query_terms = set(terms(query))
    frequencies = Counter(t for doc in tokenized for t in set(doc))
    average_length = sum(map(len, tokenized)) / len(tokenized) or 1
    scores = []
    for i, tokens in enumerate(tokenized):
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = counts[term]
            inverse_frequency = math.log(
                1 + (len(passages) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            denominator = frequency + 1.5 * (0.25 + 0.75 * len(tokens) / average_length)
            score += inverse_frequency * frequency * 2.5 / denominator
        if score > 0:
            scores.append((score, i))
    return [passages[i] for _, i in sorted(scores, reverse=True)[:k]]


def quality_ok(text):
    if len(text.strip()) < 8:
        return False
    invalid = text.count("\ufffd") + sum(ord(c) < 32 and c not in "\n\r\t" for c in text)
    return invalid / max(len(text), 1) <= 0.02


def retrieve(store, query, k_recall=30, k_final=6, reranker=None):
    if k_recall < 1 or k_final < 1:
        raise ValueError("召回和重排数量必须为正数")
    snapshot = store.snapshot()
    passages, _, _ = snapshot
    dense = store.similarity_search(query, k=k_recall, snapshot=snapshot)
    sparse = lexical_search(passages, query, k_recall)
    fusion, by_id = {}, {}
    for candidates in (dense, sparse):
        for rank, passage in enumerate(candidates, 1):
            key = passage.metadata["chunk_id"]
            by_id[key] = passage
            fusion[key] = fusion.get(key, 0) + 1 / (60 + rank)
    candidates, seen = [], set()
    for key in sorted(fusion, key=fusion.get, reverse=True):
        passage = by_id[key]
        normalized = re.sub(r"\s+", "", passage.page_content)
        if quality_ok(passage.page_content) and normalized not in seen:
            candidates.append(passage)
            seen.add(normalized)
        if len(candidates) >= k_recall:
            break
    if not candidates:
        return []
    if reranker is None:
        from app.rerank import rerank

        reranker = rerank
    ranked = reranker(query, [p.page_content for p in candidates], top_n=k_final)
    return [
        (candidates[i], float(score))
        for i, score in ranked
        if 0 <= i < len(candidates) and score >= 0.05
    ]
