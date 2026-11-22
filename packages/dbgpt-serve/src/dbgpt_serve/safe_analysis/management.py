"""Access and credentials for the local workbench's owner-only settings."""

import base64
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, SecretStr

from .contracts import AnalysisError

MODEL_ENDPOINTS = {
    "chat": ("chat/completions", "对话模型"),
    "embedding": ("embeddings", "向量模型"),
    "rerank": ("rerank", "重排模型"),
}
LOCAL_MODEL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MODEL_PROBE_TIMEOUT = 30


def model_http_options(url: str, *, timeout: int) -> dict:
    """Honor the host's proxy for remote providers, never for loopback."""
    return {
        "timeout": timeout,
        "follow_redirects": False,
        "trust_env": urlsplit(url).hostname not in LOCAL_MODEL_HOSTS,
    }


def model_endpoint(url: str, kind: str) -> str:
    """Accept an API prefix or its already complete endpoint."""
    url = url.rstrip("/")
    path = urlsplit(url).path
    for candidate, (suffix, label) in MODEL_ENDPOINTS.items():
        if path.endswith(f"/{suffix}"):
            if candidate != kind:
                raise AnalysisError(f"/{suffix} 是{label}接口，请填到「{label}」")
            return url
    return f"{url}/{MODEL_ENDPOINTS[kind][0]}"


def is_manager(token: str) -> bool:
    expected = os.environ.get("DATA_AGENT_ADMIN_TOKEN") or os.environ.get(
        "DATA_AGENT_TOKEN", ""
    )
    return len(expected) >= 32 and hmac.compare_digest(
        token.encode(), expected.encode()
    )


class ModelConnection(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    keyless: bool = False


class ModelSettings:
    """One persisted, server-side override; no credential is returned to the UI."""

    def __init__(self, path: Path, *, kind: str = "chat"):
        self.path = path
        self.kind = kind
        self.current = None
        self.read_failed = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ciphertext = data.pop("encrypted_key", None)
            if ciphertext:
                data["api_key"] = self._cipher().decrypt(ciphertext.encode()).decode()
            self.current = self.prepare(ModelConnection.model_validate(data))
        except FileNotFoundError:
            self.read_failed = path.exists()
        except (OSError, ValueError, TypeError, AttributeError, InvalidToken):
            # A broken override must not prevent access to the settings page.
            self.read_failed = True

    def _cipher(self, create=False):
        secret = os.environ.get("DATA_AGENT_ENCRYPT_KEY")
        if secret:
            return Fernet(
                base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
            )
        key_path = self.path.with_suffix(".key")
        if create:
            try:
                fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as file:
                    file.write(Fernet.generate_key())
        return Fernet(key_path.read_bytes())

    def public(self, default_model):
        config = self.current
        return {
            "model": config.model if config else default_model,
            "base_url": config.base_url if config else "",
            "has_key": bool(config and config.api_key),
            "active": config is not None,
            "keyless": bool(config and config.keyless),
            "read_failed": self.read_failed,
        }

    def prepare(self, config: ModelConnection):
        url = config.base_url.strip().rstrip("/")
        model = config.model.strip()
        try:
            parts = urlsplit(url)
            _ = parts.port
            host = parts.hostname
        except ValueError:
            host = None
        if (
            not host
            or parts.username is not None
            or parts.password is not None
            or "?" in url
            or "#" in url
            or "\\" in url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or (
                parts.scheme != "https"
                and not (parts.scheme == "http" and host in LOCAL_MODEL_HOSTS)
            )
        ):
            raise AnalysisError(
                "请填写 HTTPS Base URL；本机模型可使用 HTTP。"
                "地址中不要带凭据、查询参数或片段。"
            )
        endpoint = model_endpoint(url, self.kind)
        if not model:
            raise AnalysisError("请填写模型名称")
        key = config.api_key
        if config.keyless:
            if host not in LOCAL_MODEL_HOSTS:
                raise AnalysisError("免密钥模式仅支持本机模型")
            key = None
        elif not key or not key.get_secret_value():
            current = self.current
            if (
                not current
                or not current.api_key
                or model_endpoint(current.base_url, self.kind) != endpoint
            ):
                raise AnalysisError("请输入此模型服务的 API Key")
            key = current.api_key
        if key and any(not 33 <= ord(char) <= 126 for char in key.get_secret_value()):
            raise AnalysisError("API Key 不能包含空格、换行或非 ASCII 字符")
        return ModelConnection(
            base_url=url, model=model, api_key=key, keyless=config.keyless
        )

    def save(self, config: ModelConnection):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = config.model_dump(exclude={"api_key"})
        if config.api_key:
            data["encrypted_key"] = (
                self._cipher(create=True)
                .encrypt(config.api_key.get_secret_value().encode())
                .decode()
            )
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as file:
                temp = Path(file.name)
                json.dump(data, file, ensure_ascii=False)
            temp.replace(self.path)
        finally:
            if temp:
                temp.unlink(missing_ok=True)
        self.current = config
        self.read_failed = False

    def reset(self):
        self.path.unlink(missing_ok=True)
        self.current = None
        self.read_failed = False


async def complete(config, system, user, *, probe=False):
    url = model_endpoint(config.base_url, "chat")
    headers = {}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key.get_secret_value()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    try:
        async with httpx.AsyncClient(
            **model_http_options(url, timeout=MODEL_PROBE_TIMEOUT if probe else 45)
        ) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": config.model,
                    "messages": messages,
                    "max_tokens": 64 if probe else 1600,
                },
            )
            if response.status_code in {401, 403}:
                raise AnalysisError("模型服务拒绝了访问，请检查 API Key 和模型权限")
            if response.status_code == 404:
                raise AnalysisError(
                    "模型服务找不到接口或模型，请检查 Base URL 和模型名"
                )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError
            return text
    except httpx.TimeoutException:
        raise AnalysisError("模型服务响应超时，请稍后重试") from None
    except (httpx.HTTPError, ValueError, TypeError, IndexError, KeyError):
        raise AnalysisError("模型服务未返回有效响应，请检查接口地址和模型") from None
