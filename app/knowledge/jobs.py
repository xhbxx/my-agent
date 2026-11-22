"""网页入库任务：后台执行，页面轮询进度，重复启动时复用现有任务。"""

import threading

from app.config import safe_error
from .ingest import document_paths, ingest_documents
from .runtime import get_store


class IndexBuild:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "running": False,
            "status": "idle",
            "processed": 0,
            "total": 0,
            "failed": 0,
            "message": "尚未开始建立知识库。",
        }

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    def start(self, data_dir):
        paths = document_paths(data_dir)
        if not paths:
            raise ValueError("data目录中没有可读取的PDF、Markdown、TXT或JSON文件。")
        with self._lock:
            if self._state["running"]:
                return dict(self._state)
            self._state = {
                "running": True,
                "status": "running",
                "processed": 0,
                "total": len(paths),
                "failed": 0,
                "message": "正在解析资料并建立索引，请保持服务运行。",
            }
        worker = threading.Thread(target=self._run, args=(data_dir, paths), daemon=True)
        try:
            worker.start()
        except Exception:
            self._update(running=False, status="failed", message="任务未能启动，请重试。")
            raise
        return self.snapshot()

    def _run(self, data_dir, paths):
        failed = 0
        last_error = ""
        try:
            for count, row in enumerate(
                ingest_documents(
                    data_dir,
                    get_store(),
                    documents=paths,
                    on_start=lambda source: self._update(message=f"正在处理 {source}"),
                ),
                1,
            ):
                if row.get("state") == "failed":
                    failed += 1
                    message = f"{row['source']}：{row['error']}"
                    last_error = message
                else:
                    message = f"已处理 {row['source']}"
                self._update(processed=count, failed=failed, message=message)
                if row.get("error_type") in {"authentication", "configuration"}:
                    self._update(status="failed", message=row["error"])
                    return
            self._update(
                status="failed" if failed else "complete",
                message=(
                    f"有{failed}份资料处理失败。{last_error}；修正后可重新建立。"
                    if failed
                    else "知识库已就绪，可以返回问答页面。"
                ),
            )
        except Exception as error:
            self._update(status="failed", message=safe_error(error))
        finally:
            self._update(running=False)
