# DATA-AGENT 安全数据分析模块

目标：在 DB-GPT 的模型服务、知识解析和向量存储之上，提供一条可以顺着函数阅读的安全分析链路。

**先读 `service.py` 的 `AnalysisService.analyze()`。** 它负责调度；检索、校验、执行各自放在独立文件中。

```text
浏览器工作台 / HTTP / MCP
    ↓
身份认证与数据源授权
    ↓
Schema / 指标 / 文档检索 → Skill → DB-GPT 模型生成 SQL
    ↓
SQLGlot AST 与作用域校验
    ↓
EXPLAIN → 需要确认时返回；用户确认后重新校验
    ↓
专用连接池 → 只读执行 → 结果脱敏
    ↓
SSE 结果、图表数据与模型解释
```

| 文件 | 负责的事情 | 主要入口 |
| --- | --- | --- |
| `contracts.py` | 简单的数据结构与可公开错误 | `SourcePolicy` |
| `config.py` | 环境变量、身份与数据源白名单 | `AnalysisSettings` |
| `runtime.py` | 组装模块 | `build_service()` |
| `service.py` | 主流程与分支 | `analyze()` |
| `knowledge.py` | 复用 DB-GPT 解析口径文档 | `load_knowledge()` |
| `retrieval.py` | 授权 Schema、BM25、向量、RRF | `retrieve()` |
| `skills.py` / `skills/` | 载入三个受信任的分析 Skill | `get_skill()` |
| `model.py` | 使用 DB-GPT 已有模型服务 | `generate_sql()` / `explain()` |
| `guard.py` | AST、CTE/子查询作用域、表列权限、LIMIT | `validate_sql()` |
| `executor.py` | 专用连接池、只读事务、EXPLAIN、超时 | `assess()` / `query()` |
| `audit.py` | 一次性确认与最小审计记录 | `issue()` / `consume()` |
| `results.py` | 统一安全结果、图表规格 | `sanitize()` |
| `import_table.py` | 管理员离线导入 CSV/XLSX 到 DuckDB | `import_table()` |
| `mcp_server.py` | 将 MCP 工具适配到同一 HTTP 链路 | `create_mcp()` |

HTTP 适配层在 `dbgpt-app/src/dbgpt_app/openapi/api_v1/safe_analysis_api.py`，
挂载于 `/api/v1/safe-analysis`。

页面在同一 DB-GPT 服务的 `/safe-analysis/`，前端代码独立放在
`packages/dbgpt-app/src/dbgpt_app/safe_analysis_web/`。
在仓库根目录运行 `.\examples\safe_analysis\start.ps1`，等待启动后打开页面，输入脚本输出的工作台令牌。
当前示例启用本地持久化 Qdrant，不依赖 Docker；Embedding 复用原 DB-GPT 模型配置。

运行、配置、阅读顺序与边界见仓库根目录的
[`learn/SAFE_ANALYSIS_GUIDE_CN.md`](../../../../../learn/SAFE_ANALYSIS_GUIDE_CN.md)。
接口约定见 [`learn/SAFE_ANALYSIS_API_CN.md`](../../../../../learn/SAFE_ANALYSIS_API_CN.md)。
