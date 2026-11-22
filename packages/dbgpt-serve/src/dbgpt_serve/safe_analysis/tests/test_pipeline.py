"""Behavior tests: nested SQL permissions, real SQLite execution, approvals.

These are correctness/security tests, not an LLM accuracy benchmark.
"""

import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dbgpt_serve.safe_analysis.audit import AuditStore
from dbgpt_serve.safe_analysis.config import AnalysisSettings
from dbgpt_serve.safe_analysis.contracts import (
    AnalysisError,
    Principal,
    QueryResult,
    SourcePolicy,
)
from dbgpt_serve.safe_analysis.executor import ReadOnlyExecutor
from dbgpt_serve.safe_analysis.guard import validate_sql
from dbgpt_serve.safe_analysis.results import sanitize
from dbgpt_serve.safe_analysis.retrieval import ContextRetriever, fuse_rankings
from dbgpt_serve.safe_analysis.service import AnalysisService


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "sales.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT, "
            "amount REAL, secret TEXT);"
            "INSERT INTO sales VALUES (1,'East',10,'private-a'),"
            "(2,'West',20,'private-b'),(3,'East',30,'private-c');"
            "CREATE TABLE forbidden (id INTEGER);"
        )
    return SourcePolicy(
        name="sales",
        url="sqlite:///" + path.as_posix(),
        tables={
            "sales": {"id": "INT", "region": "TEXT", "amount": "REAL", "secret": "TEXT"}
        },
        blocked_columns=("secret",),
        masked_labels=("email",),
        metrics=["销售额 = SUM(amount)，按 region 分组"],
        max_rows=2,
        version="policy-1",
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(amount) AS total FROM sales",
        "WITH t AS (SELECT amount FROM sales) SELECT AVG(amount) FROM t",
        "SELECT s.region, SUM(s.amount) FROM sales s GROUP BY s.region",
        "SELECT id FROM sales WHERE amount > (SELECT AVG(amount) FROM sales)",
        "SELECT id FROM sales WHERE amount >= 10 "
        "AND (region = 'East' OR region = 'West')",
        "SELECT id FROM sales UNION ALL SELECT id FROM sales",
        "SELECT id FROM sales ORDER BY id LIMIT 1",
    ],
)
def test_valid_sql_executes(policy, sql):
    query = validate_sql(sql, policy)
    executor = ReadOnlyExecutor(policy)
    try:
        assert executor.query(query.sql, threading.Event()).rows
        assert validate_sql(query.sql, policy).digest == query.digest
    finally:
        executor.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM sales",
        "SELECT id FROM sales; DELETE FROM sales",
        "WITH hidden AS (DELETE FROM sales RETURNING id) SELECT * FROM hidden",
        "SELECT * FROM forbidden",
        "WITH t AS (SELECT id FROM forbidden) SELECT id FROM t",
        "SELECT id FROM sales WHERE id IN (SELECT id FROM forbidden)",
        "SELECT secret AS harmless FROM sales",
        "SELECT amount FROM sales WHERE secret='x'",
        "SELECT amount FROM sales ORDER BY secret",
        "SELECT SUM(amount) FROM sales GROUP BY secret",
        "WITH t AS (SELECT secret AS x FROM sales) SELECT x FROM t",
        "SELECT SUBSTR(secret, 1, 1) AS harmless FROM sales",
        "SELECT s.id FROM sales s WHERE s.id=(SELECT s.secret FROM sales t LIMIT 1)",
        "SELECT * FROM other.sales",
        "SELECT load_extension('x') FROM sales",
        "SELECT pg_sleep(5) FROM sales",
        "SELECT custom.sum(amount) FROM sales",
        "SELECT id INTO outfile FROM sales",
        "SELECT id FROM sales FOR UPDATE",
        "WITH RECURSIVE t AS (SELECT id FROM sales UNION ALL SELECT id FROM t) "
        "SELECT * FROM t",
        "SELECT id FROM sales LIMIT 1 OFFSET 10001",
    ],
)
def test_rejects_unsafe_sql(policy, sql):
    with pytest.raises(AnalysisError):
        validate_sql(sql, policy)


def test_star_expands_without_sensitive_column(policy):
    query = validate_sql("SELECT * FROM sales", policy)
    assert "secret" not in query.sql
    assert query.tables == ("sales",)


def test_row_cap_and_readonly_database(policy):
    executor = ReadOnlyExecutor(policy)
    try:
        query = validate_sql("SELECT id FROM sales", policy)
        result = executor.query(query.sql, threading.Event())
        assert len(result.rows) == 2 and result.truncated
        # Even a guard bypass cannot write through this connection.
        with pytest.raises(AnalysisError):
            executor.query("DELETE FROM sales", threading.Event())
    finally:
        executor.close()


def test_cancelled_query_does_not_execute(policy):
    executor = ReadOnlyExecutor(policy)
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(AnalysisError, match="取消"):
            executor.query("SELECT id FROM sales", cancel)
    finally:
        executor.close()


def test_sensitive_result_and_values(policy):
    raw = QueryResult(
        ["EMAIL", "number", "text"], [["a@b", float("nan"), "x" * 1000]], False, 1
    )
    result = sanitize(raw, policy)
    assert result.rows == [["***", None, "x" * 500]]


def test_confirmation_bound_and_single_use(tmp_path, policy):
    audit = AuditStore(tmp_path / "audit.sqlite")
    user = Principal("alice", ("sales",))
    query = validate_sql("SELECT id FROM sales", policy)
    token = audit.issue(user, policy, query)
    for actor, source, sql in [
        (Principal("bob", ("sales",)), policy, query),
        (user, replace(policy, version="policy-2"), query),
        (user, policy, validate_sql("SELECT amount FROM sales", policy)),
    ]:
        with pytest.raises(AnalysisError):
            audit.consume(token, actor, source, sql)
    audit.consume(token, user, policy, query)
    with pytest.raises(AnalysisError):
        audit.consume(token, user, policy, query)


def test_config_authentication_and_source_acl(tmp_path, monkeypatch):
    config = {
        "sources": {
            "sales": {
                "url_env": "SAFE_TEST_URL",
                "dialect": "sqlite",
                "schema": "main",
                "tables": {"sales": {"id": "INT"}},
            }
        },
        "principals": [
            {"name": "alice", "token_env": "SAFE_TEST_TOKEN", "sources": ["sales"]}
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("SAFE_TEST_URL", "sqlite://")
    monkeypatch.setenv("SAFE_TEST_TOKEN", "x" * 32)
    settings = AnalysisSettings(str(path))
    principal = settings.authenticate("x" * 32)
    assert settings.authorize(principal, "sales").name == "sales"
    with pytest.raises(AnalysisError):
        settings.authenticate("wrong")
    with pytest.raises(AnalysisError):
        settings.authorize(principal, "other")


def test_retrieval_only_contains_authorized_schema(policy):
    retriever = ContextRetriever(policy)
    docs = retriever.retrieve("销售额 region")
    assert any(doc.kind == "metric" for doc in docs)
    assert all("secret" not in doc.content for doc in docs)
    assert fuse_rankings(["a", "b"], ["b", "c"])[0] == "b"


class FakeModel:
    async def generate_sql(self, question, evidence, policy, skill):
        return "SELECT region, SUM(amount) AS total FROM sales GROUP BY region", None

    async def explain(self, question, sql, result, skill):
        assert all("private" not in str(row) for row in result.rows)
        return "East 销售额 40，West 销售额 20。"


@pytest.fixture
def service(tmp_path, policy):
    class Settings:
        sources = {"sales": policy}
        audit_path = tmp_path / "audit.sqlite"
        model = "test"

        def authorize(self, principal, source):
            if source not in principal.sources or source not in self.sources:
                raise AnalysisError("禁止访问")
            return self.sources[source]

        def authenticate(self, token):
            if token != "test-token":
                raise AnalysisError("令牌无效")
            return Principal("alice", ("sales",))

    instance = AnalysisService(Settings(), model=FakeModel())
    yield instance
    instance.close()


@pytest.mark.asyncio
async def test_question_confirm_execute_end_to_end(service):
    user = Principal("alice", ("sales",))
    events = [
        event async for event in service.analyze(user, "sales", "按区域统计销售额")
    ]
    assert not any(event["event"] == "result" for event in events)
    confirmation = next(
        event["data"] for event in events if event["event"] == "confirmation_required"
    )
    approved = [
        event
        async for event in service.analyze(
            user,
            "sales",
            "按区域统计销售额",
            sql=confirmation["sql"],
            approval_id=confirmation["approval_id"],
        )
    ]
    result = next(event["data"] for event in approved if event["event"] == "result")
    assert sorted(result["rows"]) == [["East", 40.0], ["West", 20.0]]
    assert result["chart"]["type"] == "bar"
    assert any(event["event"] == "explanation" for event in approved)
    replay = [
        event
        async for event in service.analyze(
            user,
            "sales",
            sql=confirmation["sql"],
            approval_id=confirmation["approval_id"],
        )
    ]
    assert replay[-1]["event"] == "error"


def test_http_auth_sse_and_done(service):
    from dbgpt_app.openapi.api_v1.safe_analysis_api import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.safe_analysis_service = service
    with TestClient(app) as client:
        assert client.get("/api/v1/safe-analysis/sources").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        sources = client.get("/api/v1/safe-analysis/sources", headers=headers).json()
        assert sources["sources"] == ["sales"]
        assert "secret" not in sources["details"][0]["tables"]["sales"]
        assert (
            client.post(
                "/api/v1/safe-analysis/context",
                headers=headers,
                json={"source": "unauthorized", "question": "销售额"},
            ).status_code
            == 403
        )
        retriever = service.retrievers["sales"]
        retriever.policy = replace(retriever.policy, vector=True)
        unavailable = client.post(
            "/api/v1/safe-analysis/context",
            headers=headers,
            json={"source": "sales", "question": "销售额"},
        )
        assert unavailable.status_code == 503
        retriever.policy = replace(retriever.policy, vector=False)
        response = client.post(
            "/api/v1/safe-analysis/query",
            headers=headers,
            json={
                "source": "sales",
                "sql": "SELECT id FROM sales WHERE id=1",
                "explain": False,
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "event: result" in response.text and "event: done" in response.text
        assert "private-a" not in response.text
        invalid = client.post(
            "/api/v1/safe-analysis/query",
            headers=headers,
            json={
                "source": "sales",
                "sql": "DELETE FROM sales",
                "stream": False,
            },
        ).json()
        assert invalid["events"][-2]["event"] == "error"
        assert invalid["events"][-1]["event"] == "done"


@pytest.mark.parametrize(
    "dialect,schema", [("postgres", "public"), ("mysql", "analytics")]
)
def test_server_dialect_nested_column_guard(policy, dialect, schema):
    policy = replace(policy, dialect=dialect, schema=schema)
    query = validate_sql(
        "WITH t AS (SELECT region, amount FROM sales) "
        "SELECT region, SUM(amount) AS total FROM t GROUP BY region",
        policy,
    )
    assert schema in query.sql
    with pytest.raises(AnalysisError):
        validate_sql("WITH t AS (SELECT secret FROM sales) SELECT * FROM t", policy)


def test_duckdb_import_readonly_and_external_access(tmp_path):
    from dbgpt_serve.safe_analysis.import_table import import_table

    file = tmp_path / "sales.csv"
    file.write_text("region,amount\nEast,10\nWest,20\n", encoding="utf-8")
    report = import_table(file, tmp_path / "sales.duckdb", "sales")
    assert report.pop("imported_rows") == 2
    policy = SourcePolicy(name="files", **report)
    executor = ReadOnlyExecutor(policy)
    try:
        query = validate_sql("SELECT SUM(amount) AS total FROM sales", policy)
        assert executor.query(query.sql, threading.Event()).rows == [[30]]
        assert executor.assess(query.sql, threading.Event()).needs_confirmation
        with pytest.raises(AnalysisError):
            executor.query("DELETE FROM sales", threading.Event())
        with pytest.raises(AnalysisError):
            executor.query(
                "SELECT * FROM read_csv_auto('/nonexistent.csv')", threading.Event()
            )
    finally:
        executor.close()


def test_excel_import(tmp_path):
    import pandas as pd

    from dbgpt_serve.safe_analysis.import_table import import_table

    file = tmp_path / "sales.xlsx"
    pd.DataFrame({"region": ["East"], "amount": [10]}).to_excel(file, index=False)
    report = import_table(file, tmp_path / "sales.duckdb", "sales")
    assert report["imported_rows"] == 1
    with pytest.raises(Exception):
        import_table(file, tmp_path / "sales.duckdb", "sales")  # No overwrite.


def test_dbgpt_markdown_reader_is_in_retrieval(tmp_path, policy):
    file = tmp_path / "metric.md"
    file.write_text("# 净销售额\n净销售额必须排除取消订单。", encoding="utf-8")
    docs = ContextRetriever(replace(policy, knowledge_files=(str(file),))).retrieve(
        "净销售额"
    )
    assert any(doc.kind == "document" and "取消订单" in doc.content for doc in docs)


def test_dense_adapter_indexes_only_current_policy(policy):
    class Store:
        def load_document(self, chunks):
            self.chunks = chunks

        def similar_search(self, question, topk):
            return self.chunks[::-1]

    store = Store()
    names = []

    def factory(name):
        names.append(name)
        return store

    retriever = ContextRetriever(replace(policy, vector=True), factory)
    assert retriever.retrieve("销售额")
    assert len(names) == 1
    assert all("secret" not in chunk.content for chunk in store.chunks)
    retriever.retrieve("region")
    assert len(names) == 1  # Index created once per runtime/policy.


def test_database_timeout_interrupts_work(policy):
    with sqlite3.connect(policy.url.removeprefix("sqlite:///")) as conn:
        conn.executemany(
            "INSERT INTO sales VALUES (?, 'East', 1, 's')",
            [(i,) for i in range(4, 1004)],
        )
    executor = ReadOnlyExecutor(replace(policy, timeout_ms=10))
    try:
        with pytest.raises(AnalysisError):
            executor.query(
                "SELECT SUM(a.amount+b.amount+c.amount) FROM sales a, sales b, sales c",
                threading.Event(),
            )
    finally:
        executor.close()


@pytest.mark.asyncio
async def test_mcp_only_exposes_retrieval_and_guarded_query(mcp_remote):
    from dbgpt_serve.safe_analysis.mcp_server import create_mcp

    mcp = create_mcp(mcp_remote)
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {"retrieve_context", "query_readonly"}
    # No approval tool and no raw connector/URL tool are exposed to the model.
    result = await mcp.call_tool(
        "query_readonly", {"source": "sales", "sql": "DELETE FROM sales"}
    )
    assert "error" in str(result)


@pytest.fixture
def mcp_remote(service, monkeypatch):
    import httpx

    from dbgpt_app.openapi.api_v1.safe_analysis_api import router
    from dbgpt_serve.safe_analysis.mcp_server import RemoteAnalysisService

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.safe_analysis_service = service
    original = httpx.AsyncClient

    class LocalClient(original):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", LocalClient)
    return RemoteAnalysisService("http://test", "test-token")


@pytest.mark.asyncio
async def test_mcp_http_adapter_uses_same_api(mcp_remote):
    from dbgpt_serve.safe_analysis.mcp_server import create_mcp

    mcp = create_mcp(mcp_remote)
    docs = await mcp_remote.context("sales", "销售额")
    assert any(doc["kind"] == "metric" for doc in docs["evidence"])
    await mcp.call_tool("retrieve_context", {"source": "sales", "question": "销售额"})
    with pytest.raises(AnalysisError):
        await mcp_remote.context("unauthorized", "销售额")
    data = await mcp_remote.query("sales", "SELECT id FROM sales WHERE id=1")
    events = data["events"]
    assert next(e["data"]["rows"] for e in events if e["event"] == "result") == [[1]]
    assert events[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_example_config_and_synthetic_data(tmp_path, monkeypatch):
    import runpy

    root = Path(__file__).resolve().parents[6]
    path = tmp_path / "demo.sqlite3"
    setup = runpy.run_path(str(root / "examples/safe_analysis/setup_demo.py"))
    setup["create_demo"](path)
    monkeypatch.setenv("DATA_AGENT_SALES_URL", "sqlite:///" + path.as_posix())
    monkeypatch.setenv("DATA_AGENT_TOKEN", "x" * 64)
    settings = AnalysisSettings(str(root / "configs/safe-analysis.example.json"))
    settings.audit_path = tmp_path / "audit.sqlite3"
    service = AnalysisService(settings, model=FakeModel())
    try:
        principal = settings.authenticate("x" * 64)
        events = [
            event
            async for event in service.analyze(
                principal,
                "sales_demo",
                sql="SELECT id FROM sales_public WHERE id=1",
                explain=False,
            )
        ]
        assert next(e["data"]["rows"] for e in events if e["event"] == "result") == [
            [1]
        ]
        denied = [
            event
            async for event in service.analyze(
                principal,
                "sales_demo",
                sql="SELECT customer_email FROM sales_raw",
                explain=False,
            )
        ]
        assert denied[-1]["event"] == "error"
    finally:
        service.close()
