"""Dedicated small pools and database-enforced read-only execution.

No connector.run(): it may reuse the application's wider-privilege connection.
Each source URL must point to an independently provisioned read-only account.
"""

import json
import threading
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool

from .contracts import AnalysisError, CostAssessment, QueryResult, SourcePolicy


class ReadOnlyExecutor:
    def __init__(self, policy: SourcePolicy):
        self.policy = policy
        options = dict(
            poolclass=QueuePool,
            pool_size=policy.pool_size,
            max_overflow=0,
            pool_timeout=2,
            pool_pre_ping=True,
        )
        if policy.dialect == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 1}
        elif policy.dialect == "duckdb":
            options["connect_args"] = {
                "read_only": True,
                "config": {
                    "enable_external_access": False,
                    "threads": 1,
                    "memory_limit": "256MB",
                },
            }
        else:
            options["connect_args"] = {"connect_timeout": 5}
        self.engine = create_engine(policy.url, **options)
        actual = self.engine.dialect.name
        if actual != {"postgres": "postgresql"}.get(policy.dialect, policy.dialect):
            self.engine.dispose()
            raise AnalysisError("连接 URL 与配置的数据库方言不一致")
        if actual == "sqlite":

            @event.listens_for(self.engine, "connect")
            def readonly(dbapi, _):
                dbapi.execute("PRAGMA query_only=ON")
                dbapi.execute("PRAGMA busy_timeout=1000")

    @contextmanager
    def _connection(self, cancelled: threading.Event):
        policy = self.policy
        deadline = time.monotonic() + policy.timeout_ms / 1000
        try:
            with self.engine.connect() as conn:
                driver = conn.connection.driver_connection
                if policy.dialect == "sqlite":
                    driver.set_progress_handler(
                        lambda: int(cancelled.is_set() or time.monotonic() > deadline),
                        1000,
                    )
                # Rolling back every path avoids a leaked transaction in the pool.
                try:
                    with conn.begin():
                        if policy.dialect == "postgres":
                            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                            conn.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
                            conn.exec_driver_sql(
                                "SET LOCAL statement_timeout = "
                                f"{int(policy.timeout_ms)}"
                            )
                            conn.exec_driver_sql(
                                "SET LOCAL lock_timeout = "
                                f"{min(policy.timeout_ms, 1000)}"
                            )
                        elif policy.dialect == "mysql":
                            # Must precede the first data statement in this transaction.
                            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                            conn.exec_driver_sql(
                                "SET SESSION MAX_EXECUTION_TIME="
                                f"{int(policy.timeout_ms)}"
                            )
                            conn.exec_driver_sql("SET SESSION lock_wait_timeout=1")
                            conn.exec_driver_sql("START TRANSACTION READ ONLY")
                        if cancelled.is_set():
                            raise AnalysisError("查询已取消")
                        finished = threading.Event()

                        def watch_cancel():
                            while not finished.wait(0.05):
                                timed_out = time.monotonic() > deadline
                                if cancelled.is_set() or timed_out:
                                    if policy.dialect == "postgres":
                                        driver.cancel()
                                    elif policy.dialect in {"sqlite", "duckdb"}:
                                        driver.interrupt()
                                    # MySQL falls back to its server-side timeout.
                                    return

                        watcher = threading.Thread(target=watch_cancel, daemon=True)
                        watcher.start()
                        try:
                            yield conn
                        finally:
                            # Stop the watcher before returning this connection
                            # to the pool: cancellation must not hit another query.
                            finished.set()
                            watcher.join()
                            conn.rollback()
                finally:
                    if policy.dialect == "sqlite":
                        driver.set_progress_handler(None, 0)
        except AnalysisError:
            raise
        except Exception as exc:
            # Never serialize driver errors: they can contain URLs and data literals.
            raise AnalysisError("数据库执行失败、查询超时或连接资源繁忙") from exc

    def assess(self, sql: str, cancelled: threading.Event) -> CostAssessment:
        dialect = self.policy.dialect
        prefix = {
            "sqlite": "EXPLAIN QUERY PLAN ",
            "postgres": "EXPLAIN (FORMAT JSON) ",
            "mysql": "EXPLAIN FORMAT=JSON ",
            "duckdb": "EXPLAIN (FORMAT JSON) ",
        }[dialect]
        with self._connection(cancelled) as conn:
            plan = conn.exec_driver_sql(prefix + sql).fetchall()
        if dialect == "sqlite":
            scan = any("SCAN " in str(row[-1]).upper() for row in plan)
            return CostAssessment(
                ["SQLite 计划包含扫描，无法据此准确估计扫描行数"]
                if scan and self.policy.confirm_scans
                else []
            )
        raw = plan[0][1] if dialect == "duckdb" else plan[0][0]
        document = json.loads(raw) if isinstance(raw, str) else raw
        costs, rows, scans = [], [], []

        def visit(node):
            if isinstance(node, list):
                for child in node:
                    visit(child)
            if not isinstance(node, dict):
                return
            for key in ("Total Cost", "query_cost"):
                if key in node:
                    costs.append(float(node[key]))
            for key in ("Plan Rows", "rows_examined_per_scan", "Estimated Cardinality"):
                if key in node:
                    rows.append(int(node[key]))
            if (
                node.get("Node Type") == "Seq Scan"
                or node.get("access_type") == "ALL"
                or "SEQ_SCAN" in node.get("name", "")
            ):
                scans.append(node)
            for child in node.values():
                visit(child)

        visit(document)
        cost, count = max(costs, default=0), max(rows, default=0)
        reasons = []
        if cost >= self.policy.max_cost:
            reasons.append("优化器估计成本超过阈值（成本单位不是毫秒）")
        if count >= self.policy.scan_rows:
            reasons.append("优化器估计行数超过阈值")
        if scans and self.policy.confirm_scans:
            reasons.append("执行计划包含全表或顺序扫描")
        return CostAssessment(reasons, cost, count)

    def query(self, sql: str, cancelled: threading.Event) -> QueryResult:
        started = time.monotonic()
        with self._connection(cancelled) as conn:
            result = conn.exec_driver_sql(sql)
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchmany(self.policy.max_rows + 1)]
        return QueryResult(
            columns,
            rows[: self.policy.max_rows],
            len(rows) > self.policy.max_rows,
            round((time.monotonic() - started) * 1000, 2),
        )

    def close(self):
        self.engine.dispose()
