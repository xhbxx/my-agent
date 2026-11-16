"""Optional stdio MCP adapter, sharing the exact same authorization pipeline.

Run: python -m dbgpt_serve.safe_analysis.mcp_server
Requires the optional mcp dependency and DATA_AGENT_TOKEN.
The stdio process forwards requests to the running DB-GPT HTTP server, so it
shares its vector store, policy, model services and audit log.
The caller cannot provide credentials, URLs, user IDs or filesystem paths.
"""

import os

from .contracts import AnalysisError


class RemoteAnalysisService:
    """HTTP adapter, not another SQL implementation."""

    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/") + "/api/v1/safe-analysis"
        self.token = token

    async def _post(self, path, payload):
        import httpx

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    self.base_url + path,
                    json=payload,
                    headers={"Authorization": "Bearer " + self.token},
                )
            if response.status_code != 200:
                raise AnalysisError("安全分析服务拒绝调用，请检查令牌、权限与服务状态")
            return response.json()
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnalysisError("无法连接 DATA-AGENT 安全分析服务") from exc

    async def context(self, source, question):
        return await self._post("/context", {"source": source, "question": question})

    async def query(self, source, sql):
        return await self._post(
            "/query",
            {
                "source": source,
                "sql": sql,
                "explain": False,
                "stream": False,
            },
        )


def create_mcp(service):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("DATA-AGENT")

    @mcp.tool()
    async def retrieve_context(source: str, question: str) -> dict:
        """Retrieve only the authenticated caller's schema/metric evidence."""
        if len(question) > 4000:
            return {"error": "问题超过长度限制"}
        return await service.context(source, question)

    @mcp.tool()
    async def query_readonly(source: str, sql: str) -> dict:
        """Validate and query. Risky SQL returns a confirmation-required event.

        This tool cannot approve queries. A human must confirm through the HTTP
        interface; do not expose an auto-approve tool to the model.
        """
        return await service.query(source, sql)

    return mcp


def main():
    service = RemoteAnalysisService(
        os.environ.get("DATA_AGENT_BASE_URL", "http://127.0.0.1:5670"),
        os.environ["DATA_AGENT_TOKEN"],
    )
    create_mcp(service).run(transport="stdio")


if __name__ == "__main__":
    main()
