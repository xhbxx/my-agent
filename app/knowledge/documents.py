"""文献解析与限长分块，保留原文位置和版本标识。"""

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re


@dataclass
class Passage:
    page_content: str
    metadata: dict = field(default_factory=dict)


class TokenBudget:
    """可传入 BGE tokenizer 的 encode；默认按 UTF-8 字节作保守上限。"""

    def __init__(self, limit=512, encode=None):
        if limit < 32:
            raise ValueError("片段预算至少为32")
        self.limit = limit
        self.encode = encode or (lambda text: text.encode("utf-8"))

    def count(self, text):
        return len(self.encode(text))

    def split(self, text):
        # 在预算内找最靠后的语义边界。余文继续分块，原文不会被截掉。
        start = 0
        while start < len(text):
            low, high = start + 1, len(text)
            end = start
            while low <= high:
                middle = (low + high) // 2
                if self.count(text[start:middle]) <= self.limit:
                    end, low = middle, middle + 1
                else:
                    high = middle - 1
            if end == start:
                raise ValueError("单字符超过Token预算")
            if end < len(text):
                boundaries = [
                    m.end() + start for m in re.finditer(r"[。！？；\n]", text[start:end])
                ]
                eligible = [p for p in boundaries if p > start + (end - start) // 2]
                if eligible:
                    end = eligible[-1]
            yield start, end, text[start:end]
            start = end


def read_sections(path, allow_ocr=True):
    """每条返回正文及原始位置。JSON 接受 text/content 字段或记录数组。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if len(text.strip()) < 20 and allow_ocr:
                from app.knowledge.ocr import read_page

                text = read_page(path, page_number - 1)
            yield text, {"page": page_number, "chapter": "", "parser": "pdf"}
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        records = (
            data
            if isinstance(data, list)
            else data.get("documents", [data])
            if isinstance(data, dict)
            else [data]
        )
        for number, record in enumerate(records):
            text = record if isinstance(record, str) else record.get("text", record.get("content"))
            if not isinstance(text, str):
                raise ValueError(f"JSON第{number}条缺少text/content正文")
            yield text, {"record": number, "chapter": "", "parser": "json"}
    elif suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8-sig")
        chapter, body = "", []
        for line in text.splitlines(keepends=True):
            if line.startswith("#"):
                if body:
                    yield "".join(body), {"chapter": chapter, "parser": suffix[1:]}
                chapter, body = line.lstrip("# ").strip(), [line]
            else:
                body.append(line)
        if body:
            yield "".join(body), {"chapter": chapter, "parser": suffix[1:]}
    else:
        raise ValueError(f"不支持的文档格式 {suffix}")


def parse_document(path, source=None, budget=None, allow_ocr=True):
    path = Path(path)
    budget = budget or TokenBudget()
    source = source or path.name
    # 切块规则改变时同步修改签名，避免复用旧分块。
    version = hashlib.sha256(
        path.read_bytes() + f"paragraph-v2:{budget.limit}:ocr={allow_ocr}".encode()
    ).hexdigest()
    passages = []
    for section_number, (text, location) in enumerate(read_sections(path, allow_ocr)):
        from app.textfix import normalize_text

        text = normalize_text(text)
        for start, end, chunk in budget.split(text):
            if not chunk.strip():
                continue
            chunk_id = hashlib.sha256(
                f"{source}:{version}:{section_number}:{start}:{end}".encode()
            ).hexdigest()
            passages.append(
                Passage(
                    chunk,
                    {
                        "chunk_id": chunk_id,
                        "source": source,
                        "doc_version": version,
                        "section": section_number,
                        "start": start,
                        "end": end,
                        "tokens": budget.count(chunk),
                        "offset_space": "normalized_section",
                        **location,
                    },
                )
            )
    if not passages:
        raise ValueError(f"文档未提取到正文 {source}")
    return version, passages
