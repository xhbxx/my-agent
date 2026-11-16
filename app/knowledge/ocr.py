"""扫描页识别，按文件内容和页号缓存，识别失败保留错误。"""

import hashlib
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read_page(path, page_index):
    import numpy as np
    import pypdfium2 as pdfium
    from app.paths import BASE_DIR

    path = Path(path)
    key = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = BASE_DIR / "store" / "ocr" / key / f"{page_index}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    pdf = pdfium.PdfDocument(path)
    try:
        page = pdf[page_index]
        try:
            bitmap = page.render(scale=2)
            try:
                result, _ = _engine()(np.array(bitmap.to_pil()))
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()
    text = "\n".join(row[1] for row in result or [])
    if not text.strip():
        raise ValueError(f"OCR未识别到文字 {path.name} 第{page_index + 1}页")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text
