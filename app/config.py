"""本机API配置。读取.env，原子保存，前端只获得配置状态。"""

import os
import hashlib
from urllib.parse import urlsplit
from pathlib import Path
import tempfile
import threading

from dotenv import dotenv_values

from app.paths import BASE_DIR

CONFIG_PATH = Path(os.environ.get("HERB_CONFIG_FILE", str(BASE_DIR / ".env")))
SERVICES = {
    "llm": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "embedding": ("https://api.siliconflow.cn/v1", "BAAI/bge-m3", "SILICONFLOW_API_KEY"),
    "rerank": (
        "https://api.siliconflow.cn/v1/rerank",
        "BAAI/bge-reranker-v2-m3",
        "SILICONFLOW_API_KEY",
    ),
}
API_KEYS = (
    "DEEPSEEK_API_KEY",
    "SILICONFLOW_API_KEY",
    "LLM_API_KEY",
    "EMBEDDING_API_KEY",
    "RERANK_API_KEY",
)
CONFIG_FIELDS = set(API_KEYS) | {
    f"{name.upper()}_{field}" for name in SERVICES for field in ("BASE_URL", "MODEL")
}
_lock = threading.RLock()


def api_key(name):
    with _lock:
        values = dotenv_values(CONFIG_PATH) if CONFIG_PATH.exists() else {}
        if name in values:
            return values[name] or ""
        return os.environ.get(name, "")


def configured(name):
    value = api_key(name)
    return bool(value and "你的" not in value)


def save_keys(updates):
    """只修改受支持的模型配置；空更新由调用方过滤，其他配置保持原值。"""
    from dotenv import set_key

    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=CONFIG_PATH.parent, prefix=".env-", delete=False
        ) as temporary:
            staged = Path(temporary.name)
        try:
            if CONFIG_PATH.exists():
                staged.write_bytes(CONFIG_PATH.read_bytes())
            for name, value in updates.items():
                if name not in CONFIG_FIELDS:
                    raise ValueError("不支持的配置项")
                if len(value) > 1000 or any(ord(c) < 32 for c in value):
                    raise ValueError("配置格式不正确，请填写不超过1000字符的单行内容")
                if name.endswith("BASE_URL"):
                    value = normalize_url(value)
                set_key(staged, name, value)
            os.replace(staged, CONFIG_PATH)
        finally:
            staged.unlink(missing_ok=True)


def reset_clients():
    """保存后重新创建客户端，使下一次请求使用新密钥。"""
    from app import embed, llm
    from app.knowledge import runtime

    llm._llm = None
    embed._embeddings = None
    runtime._store = None


def safe_error(error):
    message = str(error)
    for name in API_KEYS:
        value = api_key(name)
        if value:
            message = message.replace(value, "[密钥已隐藏]")
    return message[:500]


def normalize_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("API地址须为HTTP或HTTPS地址，请将密钥单独填入Key字段")
    return value.rstrip("/")


def service_settings(name):
    default_url, default_model, legacy_key = SERVICES[name]
    prefix = name.upper()
    url = (api_key(f"{prefix}_BASE_URL") or default_url).rstrip("/")
    if name in {"llm", "embedding"}:
        suffix = "/chat/completions" if name == "llm" else "/embeddings"
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    elif not url.endswith("/rerank"):
        url += "/rerank"
    return {
        "base_url": url,
        "model": api_key(f"{prefix}_MODEL") or default_model,
        "api_key": api_key(f"{prefix}_API_KEY") or api_key(legacy_key),
    }


def service_configured(name):
    key = service_settings(name)["api_key"]
    return bool(key and "你的" not in key)


def embedding_identity():
    settings = service_settings("embedding")
    if (
        settings["base_url"] == SERVICES["embedding"][0]
        and settings["model"] == SERVICES["embedding"][1]
    ):
        return settings["model"]
    endpoint = hashlib.sha256(settings["base_url"].encode()).hexdigest()[:12]
    return f"{settings['model']}@{endpoint}"
