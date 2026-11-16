"""生成一页扫描PDF，验证实际渲染与OCR依赖，不调用远程模型。"""

import json


def main():
    from PIL import Image, ImageDraw, ImageFont
    from app.knowledge.documents import parse_document
    from app.paths import BASE_DIR

    folder = BASE_DIR / "store" / "verification"
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1400, 600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 70)
    draw.text((90, 150), "REFERENCE DOCUMENT", fill="black", font=font)
    draw.text((90, 300), "ALPHA IS A.", fill="black", font=font)
    path = folder / "ocr-scan.pdf"
    image.save(path, "PDF", resolution=150)
    _, passages = parse_document(path)
    text = "".join(p.page_content for p in passages)
    assert "ALPHA" in text.upper(), text
    assert passages[0].metadata["page"] == 1
    report = {"fixture": str(path), "recognized_text": text, "page": 1, "passed": True}
    (folder / "ocr.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
