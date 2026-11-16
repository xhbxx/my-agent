"""Initialize demo state, then replace this process with the requested service."""

import json
import os
import runpy
import secrets
import sys
from pathlib import Path


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "web"
    if mode == "mcp":
        if not os.environ.get("DATA_AGENT_TOKEN"):
            raise SystemExit(
                "MCP requires the same DATA_AGENT_TOKEN as the web service"
            )
        os.execv(
            sys.executable,
            [sys.executable, "-m", "dbgpt_serve.safe_analysis.mcp_server"],
        )
    if mode != "web":
        raise SystemExit("Choose web or mcp")

    if not os.environ.get("DATA_AGENT_TOKEN"):
        os.environ["DATA_AGENT_TOKEN"] = secrets.token_urlsafe(32)
        print(
            "DATA-AGENT workbench token: " + os.environ["DATA_AGENT_TOKEN"], flush=True
        )
    runpy.run_path("examples/safe_analysis/setup_demo.py")["create_demo"](
        Path("data/safe-analysis-demo.sqlite3")
    )
    config = Path(os.environ["DATA_AGENT_CONFIG"])
    if not config.exists():
        settings = json.loads(
            Path("configs/safe-analysis.example.json").read_text(encoding="utf-8")
        )
        settings["model"] = os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini")
        config.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    os.execvp(
        "dbgpt", ["dbgpt", "start", "webserver", "--config", "configs/data-agent.toml"]
    )


if __name__ == "__main__":
    main()
