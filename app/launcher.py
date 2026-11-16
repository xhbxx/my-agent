"""双击启动器使用的入口：复用本机服务，首次启动后打开设置页。"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
import webbrowser

from filelock import FileLock

from app.paths import BASE_DIR

SERVICE_NAME = "tcm-rag-agent"


def read_json(url):
    # 本机服务不经过系统HTTP代理。
    with build_opener(ProxyHandler({})).open(url, timeout=1) as response:
        return json.load(response)


def service_ready(port):
    try:
        return read_json(f"http://127.0.0.1:{port}/api/health").get("app") == SERVICE_NAME
    except (OSError, ValueError):
        return False


def port_available(port):
    with socket.socket() as connection:
        try:
            connection.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def page_url(port):
    base = f"http://127.0.0.1:{port}"
    try:
        status = read_json(base + "/api/settings")
        if (
            status.get("index_ready")
            and all(model.get("api_key_configured") for model in status.get("models", {}).values())
            and status.get("models")
        ):
            return base + "/"
    except (OSError, ValueError):
        pass
    return base + "/settings"


def launch(open_browser=True):
    folder = BASE_DIR / "store"
    folder.mkdir(parents=True, exist_ok=True)
    with FileLock(str(folder / "launcher.lock"), timeout=90):
        ports = range(7860, 7870)
        port = next((candidate for candidate in ports if service_ready(candidate)), None)
        if port is None:
            port = next((candidate for candidate in ports if port_available(candidate)), None)
            if port is None:
                raise RuntimeError("7860至7869端口都被占用，请关闭占用程序后重试。")
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            interpreter = (
                str(BASE_DIR / ".venv" / "Scripts" / "python.exe")
                if os.name == "nt"
                else sys.executable
            )
            with (folder / "server.log").open("ab") as log:
                process = subprocess.Popen(
                    [interpreter, "-X", "utf8", "-m", "app.server", "--port", str(port)],
                    cwd=BASE_DIR,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            deadline = time.monotonic() + 60
            while not service_ready(port):
                if process.poll() is not None:
                    raise RuntimeError("服务未能启动，请查看项目store/server.log。")
                if time.monotonic() >= deadline:
                    process.terminate()
                    raise RuntimeError("服务启动超时，请查看项目store/server.log后重试。")
                time.sleep(0.4)
            (folder / "server.pid").write_text(str(process.pid), encoding="ascii")
        url = page_url(port)
    if open_browser:
        webbrowser.open(url)
    return url


def main():
    parser = argparse.ArgumentParser(description="启动中医问答并打开浏览器")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        url = launch(open_browser=not args.no_browser)
        if sys.stdout:
            print(url)
    except Exception as error:
        if os.name == "nt" and sys.stdout is None:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, str(error), "中医问答启动失败", 0x10)
        else:
            raise


if __name__ == "__main__":
    main()
