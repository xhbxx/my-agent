"""文档入库命令，解析与索引任务分别执行。"""

import argparse
import json
from pathlib import Path

from .documents import TokenBudget, parse_document


def document_paths(data_dir):
    return [
        path
        for path in sorted(Path(data_dir).rglob("*"))
        if path.is_file()
        and path.suffix.lower() in {".pdf", ".md", ".txt", ".json"}
        and path.name != "README.md"
    ]


def ingest_documents(
    data_dir, store=None, max_tokens=512, allow_ocr=True, documents=None, on_start=None
):
    """CLI与网页共用同一入库流程，每完成一个文件返回一条状态。"""
    from app.config import safe_error

    data_dir = Path(data_dir)
    paths = document_paths(data_dir) if documents is None else documents
    for path in paths:
        source = path.relative_to(data_dir).as_posix()
        row = {"source": source}
        if on_start:
            on_start(source)
        try:
            version, passages = parse_document(path, source, TokenBudget(max_tokens), allow_ocr)
            row.update(
                version=version,
                chunks=len(passages),
                characters=sum(len(p.page_content) for p in passages),
                token_counter="utf8_byte_upper_bound",
                max_tokens=max(p.metadata["tokens"] for p in passages),
            )
            if store is not None:
                task_id = store.submit(source, version, passages)
                row.update(task_id=task_id, state=store.process(task_id))
        except Exception as error:
            row.update(state="failed", error=safe_error(error))
            if store is not None and (
                getattr(error, "status_code", None) == 401 or "Token is invalid" in str(error)
            ):
                row.update(
                    error_type="authentication",
                    error=(
                        "向量服务API密钥无效或已失效（401），已停止处理后续资料。"
                        "请在设置中核对向量模型的API地址与有效密钥，保存后重新建立知识库。"
                    ),
                )
            elif store is not None and getattr(error, "status_code", None) == 422:
                row.update(
                    error_type="configuration",
                    error=(
                        "向量服务拒绝了请求参数（422），已停止处理后续资料。"
                        "请核对向量API地址和模型名称是否属于同一服务商。"
                        "Jina地址不能使用BAAI/bge-m3；保存正确配置后重新建立知识库。"
                    ),
                )
        yield row
        if row.get("error_type") in {"authentication", "configuration"}:
            return


def main():
    from app.paths import BASE_DIR

    parser = argparse.ArgumentParser(description="文献解析、版本化入库与失败补偿")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "data")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--retry-only", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()
    if not args.data_dir.is_dir():
        parser.error("语料目录不存在")
    store = None
    if not args.report_only:
        from .runtime import get_store

        store = get_store()
    if args.retry_only:
        if store is None:
            parser.error("--retry-only需要可写入模式")
        print(json.dumps(store.retry_failed(), ensure_ascii=False))
        return []
    reports = []
    failed = False
    for row in ingest_documents(args.data_dir, store, args.max_tokens, not args.no_ocr):
        reports.append(row)
        failed = failed or row.get("state") == "failed"
        print(json.dumps(row, ensure_ascii=False))
    if args.retry and store:
        print(json.dumps({"retries": store.retry_failed()}, ensure_ascii=False))
    if failed:
        raise SystemExit("部分索引任务失败，旧版本仍可查询。运行 --retry-only 补偿。")
    return reports


if __name__ == "__main__":
    main()
