"""OpenAI兼容的大模型入口，地址、模型和密钥来自本机设置。"""

from langchain_openai import ChatOpenAI
from app.config import service_settings

_llm = None


def get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        settings = service_settings("llm")
        _llm = ChatOpenAI(**settings, temperature=0, stream_usage=False)
    return _llm
