"""OpenAI兼容向量入口，建库和查询共用相同配置。"""

from langchain_openai import OpenAIEmbeddings
from app.config import service_settings

_embeddings = None


def get_embeddings() -> OpenAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(
            **service_settings("embedding"),
            check_embedding_ctx_length=False,
        )
    return _embeddings
