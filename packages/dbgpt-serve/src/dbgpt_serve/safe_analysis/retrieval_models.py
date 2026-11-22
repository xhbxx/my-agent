"""HTTP adapters for the two optional retrieval models."""

import math

import httpx

from .contracts import AnalysisError
from .management import (
    MODEL_ENDPOINTS,
    MODEL_PROBE_TIMEOUT,
    model_endpoint,
    model_http_options,
)


def _request(config, kind, payload):
    url = model_endpoint(config.base_url, kind)
    headers = {}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key.get_secret_value()
    try:
        with httpx.Client(
            **model_http_options(url, timeout=MODEL_PROBE_TIMEOUT)
        ) as client:
            response = client.post(
                url,
                headers=headers,
                json={"model": config.model, **payload},
            )
            if response.status_code in {401, 403}:
                raise AnalysisError("访问被拒绝，请检查 API Key 和模型权限")
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise AnalysisError("模型响应超时，请检查接口是否可用") from None
    except (httpx.HTTPError, ValueError):
        raise AnalysisError(
            f"模型请求失败，请检查 /{MODEL_ENDPOINTS[kind][0]} 接口地址和模型 ID"
        ) from None


class RemoteEmbeddings:
    def __init__(self, config):
        self.config = config

    def embed_query(self, text):
        return self.embed_documents([text])[0]

    def embed_documents(self, texts):
        vectors = []
        for start in range(0, len(texts), 16):
            batch = texts[start : start + 16]
            data = _request(self.config, "embedding", {"input": batch})
            try:
                items = sorted(data["data"], key=lambda item: item["index"])
                if [item["index"] for item in items] != list(range(len(batch))):
                    raise ValueError
                for item in items:
                    vector = item["embedding"]
                    if (
                        not isinstance(vector, list)
                        or not vector
                        or any(
                            type(value) not in {float, int} or not math.isfinite(value)
                            for value in vector
                        )
                        or (vectors and len(vector) != len(vectors[0]))
                    ):
                        raise ValueError
                    vectors.append(vector)
            except (TypeError, ValueError, KeyError, OverflowError):
                raise AnalysisError("向量模型未返回有效的 embeddings 结果") from None
        return vectors


def rerank(config, question, documents):
    response = _request(config, "rerank", {"query": question, "documents": documents})
    try:
        items = response.get("results", response.get("data"))
        indexes = [item["index"] for item in items]
        if not indexes or any(
            type(index) is not int or not 0 <= index < len(documents)
            for index in indexes
        ):
            raise ValueError
    except (AttributeError, TypeError, KeyError, ValueError):
        raise AnalysisError("重排模型未返回有效的候选顺序") from None
    return list(dict.fromkeys(indexes + list(range(len(documents)))))


def probe_retrieval_model(kind, config):
    if kind == "embedding":
        RemoteEmbeddings(config).embed_query("Connection check")
    else:
        rerank(config, "Connection check", ["Test document", "Another document"])
