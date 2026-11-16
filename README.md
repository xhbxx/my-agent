# 中医领域知识检索增强问答系统

基于 [djp0417/tcm-rag-agent](https://github.com/djp0417/tcm-rag-agent) 改造的中医文献 RAG 项目，保留上游 MIT 许可证。支持文献问答、问卷式信息采集和 LangGraph 多角色方案编排，适合本地学习、演示与评测。回答用于文献学习与健康知识参考，不能替代临床诊疗。

## 功能与实现边界

- **文档入库**：PDF、Markdown、TXT、JSON 统一解析；按章节、句界和长度预算切分，保留来源、页码、章节与片段标识。OCR 版可处理扫描 PDF。当前长度预算按 UTF-8 字节保守估算，不是模型精确 tokenizer。
- **混合检索**：Faiss-HNSW 向量召回与 BM25 关键词召回，通过 RRF 融合，经过质量过滤、去重和 API 重排后得到候选证据。
- **动态补查**：模型判断证据覆盖是否充分，返回证据编号、缺口和下一次查询；最多执行三轮检索，并限制重复查询。输出引用经过核查，证据不足时提示限制。
- **分层记忆**：滑动窗口与历史摘要管理短期上下文；向量相似度、医疗词典实体权重与轮次衰减筛选长期记忆，使用大模型辅助判断冲突并隔离。这里使用词典实体识别和模型冲突判断，不包含独立训练的医疗 NER、NLI 模型。
- **多 Agent 协作**：集成上游 LangGraph 流程，完成路由、信息完整性检查，食疗、经络、运动起居角色并行生成候选内容，再汇总、审核、有限次改写。多个角色可以共用同一个模型服务。
- **索引更新一致性**：文档版本与唯一任务标识、SQLite 状态管理、单写入锁和不可变索引快照；构建成功才切换活动快照，失败保留旧版本并支持重试。
- **评估与溯源**：提供检索、记忆选择与 RAGAS 评测入口，记录引用位置和失败样本。仓库仅有格式示例数据，未验证“700 条测试集”及简历中的提升比例。
- **网页设置**：大模型、向量模型、重排模型分别设置 API 地址、模型名称和 Key，支持后台建库进度与失败重试。保存后不回显密钥。
- **MCP 适配代码**：提供检索、安全检查、体质判断、信息缺口工具与只读资源。协议服务需要另装兼容 MCP SDK，目前未完成外部客户端联调。项目未实现运行时 Skill 加载机制。

界面、会话存储、问卷和多角色流程继承上游；本次改造集中于检索与索引链路、记忆策略、引用核查、服务配置及工程整理。

## 核心链路

离线：资料 → 解析/OCR → 分块与来源标记 → 向量化 → 构建 Faiss 快照 → 原子切换活动版本。

在线：用户问题 → 上下文与记忆 → 追问改写及意图/安全检查 → 混合召回 → 过滤与重排 → 判断证据缺口并按需补查 → 生成回答 → 引用核查 → 更新历史和记忆。

网页有三个独立入口：普通 RAG 问答 `/`、工具调用与问卷 `/agent`、多角色方案流程。它们并非每次请求都依次执行。

从 [核心链路导读](START_HERE.md) 开始；逐模块学习见 [源码课程](tutorial.md)，实现边界与验证记录见 [VERIFICATION.md](VERIFICATION.md)。

## Windows 本地启动

要求 Python 3.12。首次拉取项目需要创建环境和安装依赖，`.venv` 不随 Git 分发：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

然后双击根目录的 `启动中医问答.cmd`。启动器在后台启动本机服务并打开浏览器；重复双击会复用已有服务。默认端口为 7860，被占用时尝试 7861 至 7869，日志位于 `store/server.log`。

首次进入设置页，分别填写三组服务的地址、模型名称和 Key，点击“保存设置”，再点击“建立 / 更新知识库”。建库成功后返回问答。大模型与向量模型使用 OpenAI 兼容接口；重排接口需要兼容 Cohere 的 `results/index/relevance_score` 格式。401 / Token is invalid 表示服务凭证无效，需要修正对应 Key 后重新建库。

本地密钥保存在 Git 忽略的 `.env`；Key 留空会保留已保存值。也可复制 `.env.example` 手动配置。增加资料时放入 `data/`，再更新知识库。切换向量模型后需要重新建库。

手动启动或检查解析：

```powershell
# 只解析分块，不调用模型 API
.venv\Scripts\python.exe -X utf8 -m app.index --report-only --no-ocr
# 建库
.venv\Scripts\python.exe -X utf8 -m app.index
# 启动 Web 服务
.venv\Scripts\python.exe -X utf8 -m app.server --port 7860
# 命令行问答
.venv\Scripts\python.exe -X utf8 -m app.ask
```

扫描 PDF 需要额外安装 `requirements-ocr.txt`；RAGAS 需要 `requirements-eval.txt`；开发检查需要 `requirements-dev.txt`。

## Docker 启动

需要先启动 Docker Desktop 并启用 Linux 容器。基础镜像使用外部模型 API，不在容器里下载大模型，也不需要 GPU。

```powershell
# 构建基础镜像并启动
 docker compose up -d --build
# 查看状态和日志
 docker compose ps
 docker compose logs -f herb
```

打开 **http://localhost:7861/settings**，填写三组 API 配置并建库，然后访问 **http://localhost:7861/**。容器映射到本机 7861，便于和默认 7860 的本地版本区分；如端口被占用，可修改 `compose.yaml` 的端口左侧。

Compose 将 `data/` 只读挂载到容器；API 配置保存在 `herb-config` 命名卷，索引与会话保存在 `herb-store` 命名卷。更新镜像和普通 `docker compose down` 会保留这些数据。镜像不打包本机 `.env`、向量库、聊天记录和虚拟环境。容器里的 Key 需要首次单独配置。

需要识别扫描 PDF 时，构建带 OCR 依赖的版本：

```powershell
$env:HERB_IMAGE_TARGET = 'ocr'
docker compose up -d --build
```

也可以分别构建两种本地镜像：

```powershell
docker build --target runtime -t herb-qa:base .
docker build --target ocr -t herb-qa:ocr .
# 停止 Compose 服务，保留配置与索引
docker compose down
```

设置接口限定本机访问，因此请使用 `localhost` 或 `127.0.0.1`。当前配置用于单机运行；没有提供公网账号认证和多用户隔离。镜像构建需要联网拉取 Python 基础镜像、系统包和 Python 依赖。Docker 构建文件已提供，具体构建结果以本次交付说明为准。

## 模块导航

| 路径 | 职责 |
|---|---|
| `app/knowledge/documents.py`、`ocr.py` | 多格式解析、OCR、分块和原文位置 |
| `app/knowledge/store.py`、`runtime.py` | 版本化任务、Faiss 快照与共享实例 |
| `app/knowledge/retrieval.py`、`decision.py` | 混合召回、重排、证据缺口判断与补查 |
| `app/rag.py`、`knowledge/citations.py` | 统一问答链路与引用核查 |
| `app/memory.py`、`knowledge/memory_policy.py` | 上下文摘要、记忆筛选与冲突隔离 |
| `app/agent/`、`app/multiagent/` | 问卷状态机、工具调用、LangGraph 编排 |
| `app/config.py`、`app/web_settings.py` | 三组模型配置与后台建库接口 |
| `app/server.py`、`web/` | FastAPI、SSE 与网页 |
| `app/mcp/` | 对外工具和只读资源适配 |

## 检查与评测

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check app tools tests
# RAGAS 需要额外评测依赖、可用 API 和已建立的知识库
.venv\Scripts\python.exe -m app.knowledge.evaluate --dataset eval/local_example.jsonl --generate --ragas
.venv\Scripts\python.exe -m app.knowledge.evaluate_memory --dataset eval/memory_example.jsonl
```

示例 JSONL 用于说明评测格式，不能证明质量提升。`eval/report_*` 是上游历史记录。性能结果需要区分纯索引查询耗时与包含向量服务、重排和生成的完整请求耗时。

仓库附带 6 份上游知识卡片。本机额外下载的《神农本草经》TXT 按语料忽略规则不随 Git 分发，获取来源和大小见 [语料说明](data/README.md)。
