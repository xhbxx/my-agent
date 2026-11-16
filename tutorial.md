# 中医问答项目源码课程

核验日期：2026-11-16。项目：`E:\study\yixue\tcm-rag-agent`。

基线为上游提交`9cc954e`；当前包含未提交的本地改造，课程以当前源码为准，不能仅按Git提交判断行为。目标是理解RAG、意图识别、记忆、工具Agent、LangGraph、Skill与MCP的职责和真实调用关系。

前置基础：Python函数与类、字典和JSON、异常、HTTP请求；读网页链路时认识生成器yield和SSE即可。课程当前均为大纲，不自动展开全部课程或生成题库。可按编号继续学习。

## 先确认三条业务入口

| 请求入口 | 执行主线 | 用途 |
|---|---|---|
| `/api/conversations/{conv_id}/chat` | RAGSession._run_turn | 文献问答 |
| `/api/agent/{conv_id}/chat` | ConsultAgent.ask_stream或ask_quick | 体质问卷与工具调用 |
| `/api/plan/{conv_id}/chat` | LangGraph run_pipeline | 多角色方案编排 |

Web请求没有统一先经过MCP，也没有先通过一个全局意图模型再选择这三个API。页面调用哪个API先确定流程，流程内部再做相应意图判断。

## 概念与实现边界

- RAG：把检索到的文献证据放进生成上下文；本项目还加了证据充分性判断与引用核查。
- Agent：根据当前输入、状态与工具反馈，选择下一步行动。主检索循环和问诊工具Agent的控制方式不同。
- Tool：一个具体可执行能力，例如检索、记录答案和计分；实际执行者是Python代码。
- LangGraph：在多角色方案中管理状态、节点、条件分支、并行和审核回环。
- Skill：通常指包含操作说明、适用条件及可选脚本/资料的可复用工作流程包。本项目没有Skill加载执行机制。当前讲解使用的project-guide是Codex侧导学技能，不属于本项目网页运行时。
- MCP：外部AI应用发现、调用工具和读取资源的协议边界。本项目有服务端适配代码，当前虚拟环境未安装SDK，业务层测试通过不等于外部客户端已接通。

资料现状、模型配置与实测范围另见START_HERE.md和VERIFICATION.md。百分比提升和700条测试集仍不能作为已验证成果。

## 第01课：一句文献问题怎样变成带出处的回答？

状态：大纲。读完应能独立回答标题中的问题。

主链：网页发送问题 → api_chat → RAGSession.ask_stream → _run_turn → 引用核查 → SSE与消息落库。

必读顺序：
1. `app/server.py::api_chat`：问题、会话编号；先存用户消息，再创建会话对象并交给后台线程。
2. `app/rag.py::RAGSession._run_turn`：原始问题；依次组装上下文、改写追问、前置扫描、检索、生成与核查，输出事件。
3. `app/knowledge/citations.py::verify_answer`：回答和本轮证据；检查编号范围及模型判定的支持关系。
4. `app/server.py::_sse_stream`：后台队列事件；转为浏览器SSE，并通过回调保存最终回答与出处。

选读验证：tests/test_entrypoints.py：CLI、Web、同步与流式一致性，以及核查先于回答输出。

暂缓与边界：检索算法交给03课，记忆交给05课。

## 第02课：一本资料怎样安全进入可查询的知识库？

状态：大纲。读完应能独立回答标题中的问题。

主链：网页建库或CLI → ingest_documents → parse_document → submit → process → 完整快照发布。

必读顺序：
1. `app/knowledge/ingest.py::ingest_documents`：资料目录和知识库；遍历文件并返回逐文件状态。
2. `app/knowledge/documents.py::parse_document`：文档；解析、归一化、按预算切分，得到正文与出处元数据。
3. `app/knowledge/store.py::KnowledgeStore.submit`：来源、版本、片段；登记期望版本及唯一任务。
4. `app/knowledge/store.py::KnowledgeStore.process`：任务编号；文件锁下构建快照，SQLite事务切换可见指针。

选读验证：tests/test_knowledge.py：重复提交、版本回退、失败保留旧快照、补偿与并发。

暂缓与边界：OCR渲染细节暂缓；向量模型切换可在掌握版本一致性后读tests/test_model_config.py。

## 第03课：怎样检索、重排，并决定是否还要补查？

状态：大纲。读完应能独立回答标题中的问题。

主链：独立问题 → retrieve_with_decision → retrieve → Faiss与BM25 → 融合过滤重排 → 审核缺口 → 补查或停止。

必读顺序：
1. `app/knowledge/decision.py::retrieve_with_decision`：问题；初始化累积证据、已用查询和行动记录。
2. `app/knowledge/retrieval.py::retrieve`：查询与同一快照；两路召回、RRF融合、质量过滤、去重和重排。
3. `app/knowledge/store.py::KnowledgeStore.similarity_search`：问题向量；查询当前HNSW，返回片段。
4. `app/rerank.py::rerank`：问题与候选正文；调用设置中的重排服务，返回候选位置与相关分数。
5. `app/knowledge/decision.py::ask_json`：证据审核提示；模型返回sufficient、supported_ids、missing、next_query，由代码校验与限轮。

选读验证：tests/test_knowledge.py：缺口触发下一轮、重复查询终止、非法证据编号。

暂缓与边界：先理解候选集合怎样变化，再看BM25公式和HNSW参数。

## 第04课：问知识、求建议、要方案，分别怎么识别？

状态：大纲。读完应能独立回答标题中的问题。

主链：用户文本 → 规则意图判断或多角色路由 → 前置信息检查 → 对应处理分支。

必读顺序：
1. `app/rag.py::RAGSession._safety_guard`：问题和档案；调用规则意图识别，计算信息缺口与追问。
2. `app/inquiry.py::intent_of`：文本；输出knowledge或advice，默认knowledge。
3. `app/inquiry.py::require_slots`：意图；选择需要检查的信息槽位。
4. `app/multiagent/nodes.py::router_node`：多角色入口的用户输入和会话状态；先规则扫描红旗，再LLM语义路由，无效输出走关键词兜底。
5. `app/multiagent/nodes.py::route_after_router`：路由结果；选择collector、fast、urgent或need_consult。

选读验证：tools/test_inquiry.py与app/multiagent/selftest.py：知识问答、追问及路由行为。

暂缓与边界：这两处不是全站统一路由器：当前网页请求先由所调用的API入口确定所属流程。

## 第05课：追问怎样接上历史，哪些内容能进入长期记忆？

状态：大纲。读完应能独立回答标题中的问题。

主链：会话编号与问题 → build_context → 窗口、摘要、档案和记忆 → 问题改写 → 本轮结束后维护。

必读顺序：
1. `app/memory.py::MemoryManager.build_context`：会话与问题；组装近期原文、摘要、档案与允许作用域内的记忆。
2. `app/memory.py::MemoryManager.recall`：问题和候选条目；按相关性、衰减重要度、状态筛选。
3. `app/knowledge/memory_policy.py::MemoryPolicy`：候选陈述或查询；实体加权、向量相似度、NLI准入与冲突隔离。
4. `app/rag.py::RAGSession._condense`：近期历史与追问；生成独立检索问题。
5. `app/rag.py::RAGSession._post_turn`：本轮问答；抽取记忆并按条件触发历史压缩。

选读验证：tests/test_knowledge.py：作用域隔离、冲突、重要度与轮次衰减。

暂缓与边界：CLI只维护内存窗口；完整的持久化记忆学习场景使用网页会话。

## 第06课：Agent怎样选择工具，而不是直接生成回答？

状态：大纲。读完应能独立回答标题中的问题。

主链：问诊API → ConsultAgent.ask_stream → ToolBox.schemas → bind_tools → tool_calls → ToolBox.call → ToolMessage → 下一轮。

必读顺序：
1. `app/server.py::api_agent_chat`：问诊消息；普通文本交给工具Agent，点选分数可走ask_quick。
2. `app/agent/agent.py::ConsultAgent.ask_stream`：消息与问卷状态；按当前阶段绑定工具，最多执行6轮工具决策。
3. `app/agent/tools.py::ToolBox.schemas`：问卷阶段；只返回该阶段允许模型看到的工具定义。
4. `app/agent/tools.py::ToolBox.call`：工具名和参数；分发到Python工具，结果作为JSON反馈给模型。
5. `app/agent/state.py::ConsultState`：答案和阶段；持久化问卷进度。

选读验证：tools/test_architecture.py与tools/test_fresh_session.py：状态约束和问卷流程。

暂缓与边界：先看工具循环，再看量表的具体计分规则。ToolBox不是Skill加载器，也不是MCP客户端。

## 第07课：多角色方案为什么需要LangGraph？

状态：大纲。读完应能独立回答标题中的问题。

主链：方案API → run_pipeline → router → collector与diagnoser → 三个专家并行 → planner → safety → editor。

必读顺序：
1. `app/server.py::api_plan_chat`：方案请求；启动图执行线程并转发节点进度。
2. `app/multiagent/graph.py::run_pipeline`：会话编号与用户输入；创建状态并执行编译后的图。
3. `app/multiagent/graph.py::build_graph`：节点与条件边；装配分流、专家扇出扇入和审核回环。
4. `app/multiagent/nodes.py::route_after_diagnoser`：判断结果；补问、就医分支或并行专家分支。
5. `app/multiagent/nodes.py::route_after_safety`：审核结果和重写次数；决定交付、重写或降级交付。

选读验证：app/multiagent/selftest.py：使用脚本替身验证9类控制流场景。

暂缓与边界：主RAG的多轮检索是Python循环；LangGraph负责这里的多角色编排。

## 第08课：外部AI应用怎样复用本项目工具？Skill和MCP怎样区分？

状态：大纲。读完应能独立回答标题中的问题。

主链：外部MCP客户端 → 协议入口与工具注册 → tools中的业务函数 → 共用检索或规则能力 → 协议结果。

必读顺序：
1. `app/mcp/__main__.py::main`：启动参数；选择stdio或HTTP传输模式。
2. `app/mcp/server.py::mcp`：工具和资源声明；注册4个工具、3类资源。
3. `app/mcp/tools.py::tcm_search`：查询；直接执行混合召回与重排，不调用回答生成与多轮证据审核。
4. `app/mcp/resources.py::kb_manifest`：读取请求；读取当前已发布快照，返回语料清单。
5. `app/mcp/schemas.py::TOOL_TCM_SEARCH`：工具说明与参数约束；供客户端发现和调用工具。

选读验证：tools/test_mcp_tools.py为业务逻辑验证，tools/test_mcp_server.py为协议层验证入口。

暂缓与边界：当前未安装mcp SDK，协议服务尚不能直接启动。仓库无SKILL.md或Skill加载器，Skill只作为概念对照讲解，不虚构调用链。

## 建议的第一轮阅读

先按01 → 02 → 03走通文献问答，再学04与05，最后比较06、07、08。每次只追一个输入从哪里来、变成什么、交给谁；不要同时展开所有辅助函数。

当前只核验课程所列代表性主链，未做全仓库逐行讲解。量表计算、规则词表、前端模板和具体MCP客户端配置留到对应课程再按需展开。若某文件或原理看不懂，可以继续追问；大纲不替代逐段源码讲解。
