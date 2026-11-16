"""Load an explicit allowlist; missing configuration keeps the API disabled."""

import hashlib
import hmac
import json
import os
from pathlib import Path

from .contracts import AnalysisError, Principal, SourcePolicy


class AnalysisSettings:
    def __init__(self, path: str):
        self.path = Path(path).resolve()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.audit_path = self.path.parent / data.get(
            "audit_file", "safe-analysis-audit.sqlite3"
        )
        self.model = data.get("model", "")
        qdrant_path = data.get("qdrant_path")
        self.qdrant_path = (
            (self.path.parent / qdrant_path).resolve() if qdrant_path else None
        )
        self.sources = {}
        for name, source in data["sources"].items():
            source = dict(source)
            url = os.environ.get(source.pop("url_env"), "")
            if not url:
                raise AnalysisError(f"数据源 {name} 的连接环境变量尚未设置")
            if source.get("dialect") not in {"sqlite", "postgres", "mysql", "duckdb"}:
                raise AnalysisError("不支持此数据库方言")
            for key in ("max_rows", "timeout_ms", "pool_size", "scan_rows"):
                if key in source and int(source[key]) <= 0:
                    raise AnalysisError(f"{key} 必须为正整数")
            if not source.get("tables"):
                raise AnalysisError("每个数据源必须明确配置表与字段白名单")
            if not source.get("schema"):
                raise AnalysisError("必须明确配置数据库 schema")
            if source.get("max_rows", 200) > 1000:
                raise AnalysisError("单次查询最多允许返回 1000 行")
            if source.get("timeout_ms", 5000) > 60000:
                raise AnalysisError("数据库超时最多允许 60000 毫秒")
            files, fingerprints = [], []
            for filename in source.get("knowledge_files", []):
                file = (self.path.parent / filename).resolve()
                if file.suffix.lower() not in {".md", ".txt", ".pdf", ".docx"}:
                    raise AnalysisError("口径文档支持 MD/TXT/PDF/DOCX")
                if not file.is_file() or file.stat().st_size > 10 * 1024 * 1024:
                    raise AnalysisError("口径文档不存在或超过 10MB")
                files.append(str(file))
                fingerprints.append(hashlib.sha256(file.read_bytes()).hexdigest())
            source["knowledge_files"] = tuple(files)
            source["version"] = hashlib.sha256(
                json.dumps([source, url, fingerprints], sort_keys=True).encode()
            ).hexdigest()
            self.sources[name] = SourcePolicy(name=name, url=url, **source)
        self.principals = []
        for principal in data["principals"]:
            token = os.environ.get(principal["token_env"], "")
            if len(token) < 32:
                raise AnalysisError("访问令牌必须通过环境变量配置且至少 32 字符")
            self.principals.append(
                (
                    hashlib.sha256(token.encode()).digest(),
                    Principal(principal["name"], tuple(principal["sources"])),
                )
            )

    def authenticate(self, token: str) -> Principal:
        digest = hashlib.sha256(token.encode()).digest()
        for expected, principal in self.principals:
            if hmac.compare_digest(digest, expected):
                return principal
        raise AnalysisError("访问令牌无效")

    def authorize(self, principal: Principal, source: str) -> SourcePolicy:
        if source not in principal.sources or source not in self.sources:
            raise AnalysisError("没有此数据源的访问权限")
        return self.sources[source]
