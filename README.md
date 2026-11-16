# DATA-AGENT

面向多数据源的安全数据分析 Agent，基于 DB-GPT 二次开发。用户通过自然语言描述问题，系统检索表结构与业务口径，生成 SQL，经权限和成本检查后只读执行，再返回表格、图表和解释。

## 主要功能

| 模块 | 当前实现 |
| --- | --- |
| 数据接入 | SQLite 演示源；PostgreSQL/MySQL 执行适配；CSV/XLSX 离线导入 DuckDB |
| 业务知识检索 | 授权 Schema、指标定义和 PDF/DOCX/Markdown/TXT 文档；Qdrant 向量检索 + BM25 + RRF |
| 自然语言分析 | 依据问题、检索证据和 Skill 生成 SQL；信息不足时允许返回澄清问题 |
| SQL 治理 | SQLGlot AST、CTE/子查询作用域、表列白名单、函数限制与结果行数上限 |
| 受控执行 | 专用连接池、只读执行、超时取消适配、EXPLAIN 与高成本查询二次确认 |
| 安全结果 | 字段脱敏，页面、模型解释和导出共享同一份安全结果 |
| Skill | 通用、维度对比和趋势三个分析说明文件，由用户选择 |
| MCP | 通过 stdio 对外提供 retrieve_context 和 query_readonly，复用同一安全 API |
| 工作台 | 中文简洁界面，SSE 执行阶段、SQL/证据/结果页签、图表、Markdown、CSV 导出和手机菜单 |

当前是显式 Python 工作流；没有独立意图分类器、自动选择 Skill、完整 ReAct 工具循环或主动接入外部 MCP Server。界面上的管理入口部分复用原框架。

## 核心流程

```text
页面 / MCP → 认证授权 → 业务知识检索 → Skill + 模型生成 SQL
→ AST 校验 → EXPLAIN / 必要确认 → 只读执行 → 脱敏 → 表格、图表与解释
```

提供 SQL 时跳过检索和生成，但仍执行安全检查。高成本确认不占用数据库连接等待；确认请求重新检查 SQL 与执行计划。

## Windows 本地启动

需要 Python 环境和已安装的 DB-GPT 工作区依赖。已有本地环境时：

```powershell
cd E:\DB-GPT
.\examples\safe_analysis\start.ps1
```

若脚本执行策略阻止运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\safe_analysis\start.ps1
```

打开 http://127.0.0.1:5670/safe-analysis/，输入终端显示的工作台令牌。先点击“查看一条示例记录”检查数据库链路，再检查知识检索、输入自然语言问题。

工作台令牌与模型 API key 不同。令牌确认用户身份和可访问数据源；模型密钥仅配置在服务端。

已有配置的本地用户继续使用 configs/dbgpt-proxy-openai.toml。仓库发布版该文件不包含个人密钥；新环境可参考 configs/data-agent.example.toml，通过环境变量配置模型。示例模型名称必须与 configs/safe-analysis.example.json 的 model 对应。

本地补充模块依赖：

```powershell
uv pip install --python .venv/Scripts/python.exe "sqlglot>=28,<31" "rank-bm25>=0.2,<0.3" "qdrant-client>=1.17.1,<2"
```

首次完整安装仍需安装原框架工作区依赖，参见 [上游安装说明](README.DBGPT.md)。当前 uv.lock 尚未同步新增可选依赖，已装环境用已安装 CLI 启动；不要直接假设冻结锁文件包含新模块。

## Docker 启动

镜像包含后端、静态工作台和 Qdrant 本地客户端，使用远端 OpenAI 兼容模型 API，不要求本地 GPU。首次构建仍需下载原框架的完整依赖，镜像并非轻量独立后端。

```powershell
Copy-Item docker/data-agent/.env.example docker/data-agent/.env
```

编辑 docker/data-agent/.env，填写 LLM_MODEL_API_KEY、EMBEDDING_MODEL_API_KEY 和随机的 DATA_AGENT_ENCRYPT_KEY。需要时修改模型名称和 API 地址。DATA_AGENT_TOKEN 至少 32 字符；留空则启动时生成，重启后可能变化，从日志读取。

```powershell
docker compose --env-file docker/data-agent/.env -f docker/data-agent/compose.yaml up --build -d
docker compose --env-file docker/data-agent/.env -f docker/data-agent/compose.yaml logs -f
```

访问 http://127.0.0.1:5670/safe-analysis/。默认只绑定本机端口，演示数据、Qdrant 索引、审计和框架状态写入命名卷。

构建两个用途的本地镜像：

```powershell
docker build -f docker/data-agent/Dockerfile --target web -t data-agent:local .
docker build -f docker/data-agent/Dockerfile --target mcp -t data-agent-mcp:local .
```

MCP 镜像使用 stdio，需要正在运行的 Web 服务和相同的工作台令牌：

```powershell
docker run --rm -i --env-file docker/data-agent/.env -e DATA_AGENT_BASE_URL=http://host.docker.internal:5670 data-agent-mcp:local
```

MCP 模式不要加 -t；令牌留空自动生成时，先将 Web 日志中的令牌填入 .env。MCP 工具不提供自动批准高成本查询的能力。

停止服务保留数据：

```powershell
docker compose --env-file docker/data-agent/.env -f docker/data-agent/compose.yaml down
```

构建状态：已提供 Web/MCP Docker 目标与 Compose 配置，但镜像构建尚未成功。直接访问 Docker Hub 时认证服务超时，改用缓存镜像后仍在安装系统依赖时连接中断；目前按用户要求停止构建。尚未完成容器启动验收或发布到镜像仓库，部署文件需后续验证。

## 目录与阅读顺序

| 位置 | 职责 |
| --- | --- |
| packages/dbgpt-serve/src/dbgpt_serve/safe_analysis/service.py | 主流程，建议最先阅读 |
| 同目录 retrieval.py / knowledge.py | 混合检索、文档解析 |
| 同目录 model.py / skills.py / skills/ | 模型适配与分析 Skill |
| 同目录 guard.py / executor.py / audit.py / results.py | 校验、执行、确认、审计与结果处理 |
| 同目录 mcp_server.py | MCP → 安全 HTTP API 适配 |
| packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/safe_analysis_api.py | HTTP/SSE 接口 |
| packages/dbgpt-app/src/dbgpt_app/safe_analysis_web/ | DATA-AGENT 工作台 |
| docker/data-agent/ | Dockerfile、Compose、启动入口及环境变量模板 |

- [运行与阅读指南](learn/SAFE_ANALYSIS_GUIDE_CN.md)
- [接口文档](learn/SAFE_ANALYSIS_API_CN.md)
- [代码精简审查](learn/SAFE_ANALYSIS_CLEANUP_REVIEW_CN.md)

## 验证与边界

新增安全分析测试曾在本地通过 49 项，覆盖 SQL 拦截、只读执行、确认流程、HTTP/SSE、文件解析、MCP 与真实 Qdrant 本地持久化。浏览器预览验证过桌面和手机操作，使用固定测试模型和合成数据，不能据此宣称生产模型正确率、真实业务库性能或线上并发能力。

默认演示库有 10,000 行合成数据。PostgreSQL/MySQL 需真实环境联调；只读账号和数据库安全视图由管理员配置。Qdrant 本地模式用于单 Web 进程，服务端部署与 Dashboard 需另行配置。PDF 扫描件 OCR、旧版 .doc、在线上传和多租户登录不属于新增工作台已交付能力。

## 安全与开源归属

不要提交 .env、个人 API key、工作台令牌、上传数据、运行数据库及日志。Docker 构建不复制个人模型配置；Compose 通过运行时环境变量传入密钥。

本项目基于 [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) 开发，保留原框架包名、版权和 [MIT License](LICENSE)。[原项目 README](README.DBGPT.md) 保留上游介绍与安装资料。
