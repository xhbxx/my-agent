# 安全数据分析 API

## 基本约定

- 前缀：`/api/v1/safe-analysis`。
- 身份：`Authorization: Bearer <安全分析令牌>`，来自服务端配置的环境变量。
- 不接受客户端提供的 user_id、数据库 URL、凭据或文件路径来决定访问权限。
- 源权限由安全分析配置中的 principal 与 sources 决定。
- 认证失败返回 401，未启用或配置失败返回 503。
- 查询开始前的参数或授权错误返回 400；流开始后的错误通过 error 事件返回。
- 这是新增接口；与原 `/chat/react-agent` 的协议不兼容。
- 浏览器工作台：`/`，同源请求本接口，无需手工调用命令行。

## 1. GET /sources

返回当前身份允许使用的数据源名称、模型名称及供页面展示的安全元数据；不返回连接 URL 或凭据。

```json
{
  "sources": ["sales_demo"],
  "model": "服务端配置的模型名",
  "details": [{
    "name": "sales_demo", "dialect": "sqlite",
    "tables": {"sales_public": {"id": "INTEGER", "amount": "REAL"}},
    "metrics": ["有效销售额 = SUM(amount)，只统计 completed 记录"],
    "documents": ["metrics.md"],
    "retrieval": "Qdrant + BM25 + RRF", "index_ready": false
  }]
}
```

`tables` 仅包含授权可见字段。`documents` 仅返回当前数据源的文档名称。`index_ready` 表示本进程已经完成索引加载，
不是独立服务健康检查；页面“检查知识检索”才会发起实际检索。

## 2. POST /context

检索当前数据源下已批准的 Schema、指标和文档证据。无权限返回 403，检索服务错误返回 503。

```json
{"source": "sales_demo", "question": "有效销售额的口径是什么"}
```

```json
{
  "evidence": [
    {"id": "metric:0", "content": "有效销售额 = SUM(amount)，只统计 completed 记录", "kind": "metric"}
  ]
}
```

## 3. POST /query

### 请求

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| source | string | 必填，已授权数据源名称 |
| question | string | 自然语言问题，最长 4000 字符 |
| sql | string/null | 可选；提供时跳过生成，但仍做全部安全检查；最长 20000 字符 |
| approval_id | string/null | 高风险确认编号，需和先前返回的 SQL 一起提交 |
| skill | string | general / trend / comparison，默认 general |
| explain | boolean | 是否调用模型解释，默认 true |
| stream | boolean | SSE 或事件数组，默认 true |

question 与 sql 至少提供一项。接收确认时必须传 sql，禁止重新生成另一条 SQL。
model 由服务端配置选择，客户端不能临时传入供应商 URL 或 API key。

### 常规请求

```json
{
  "source": "sales_demo",
  "question": "按区域统计有效销售额",
  "skill": "comparison",
  "explain": true,
  "stream": true
}
```

### SSE 事件

Content-Type 为 `text/event-stream`。事件框架用空行分隔；网络分块不等于完整事件，
客户端要积累文本直到出现空行再解析 event 与 data。
请求是带 JSON body 和 Authorization 的 POST，应使用 fetch 流或相应 SSE 客户端，
不能直接用仅 GET 的原生 EventSource 代替。

```text
event: stage
data: {"name":"validation"}

event: sql
data: {"sql":"SELECT ..."}

event: result
data: {"columns":["region","total"],"rows":[["East",40]],"truncated":false,"elapsed_ms":2.1,"sql":"SELECT ...","chart":{"type":"bar","x":"region","y":"total","data":[["East",40]]}}

event: explanation
data: {"text":"East 的有效销售额为 40。"}

event: done
data: {}

```

| 事件 | 含义 |
| --- | --- |
| started | request_id，可用于请求取消 |
| stage | retrieval / generation / validation / cost_check / execution / explanation |
| context | 已授权证据列表 |
| clarification | 信息不足，需要补充问题；本次不执行 |
| sql | 校验通过并规范化后的 SQL |
| confirmation_required | 需要用户确认；本次未执行，随后 done |
| result | 脱敏表格、截断标志、SQL、耗时、可选图表规格 |
| explanation | 基于有限安全结果的模型解释 |
| warning | 查询成功，但可选解释失败 |
| error | 本次流程失败的安全错误说明 |
| done | 本次流程结束，应关闭加载状态 |

result 是权威查询结果；模型解释只使用最多 20 行且受字符预算限制的安全样本。
chart 目前只为两列且第二列为数值的结果生成柱状图规格，最多 50 行。
前端仍需用图表库渲染，不是已经生成好的图片。

### 高风险确认

```json
{
  "event": "confirmation_required",
  "data": {
    "approval_id": "一次性编号",
    "expires_in": 300,
    "sql": "规范化 SQL",
    "cost": {"reasons": ["计划包含全表扫描"], "estimated_cost": null, "estimated_rows": null}
  }
}
```

用户确认后重新调用 /query：

```json
{
  "source": "sales_demo",
  "question": "按区域统计有效销售额",
  "sql": "先前返回的规范化 SQL",
  "approval_id": "先前返回的一次性编号",
  "skill": "comparison",
  "explain": true,
  "stream": false
}
```

身份、源、SQL 或策略版本不匹配、超过 300 秒、重复消费都会拒绝。
数据库执行失败后确认也不会恢复；重新请求并由用户再次确认。

### 非流式返回

stream=false 返回 `{"events": [{"event": "...", "data": {}}]}`，语义与 SSE 一致。
HTTP 200 不代表 SQL 已执行成功，调用方必须检查 error / clarification / confirmation_required / result。

## 4. POST /requests/{request_id}/cancel

仅接受所属用户取消当前进程内的请求，未知或不属于本人返回 404。

```json
{"cancel_requested": true}
```

取消请求不是执行已终止的证明。数据库中止能力和多进程限制见运行指南。

## 5. 数据与日志边界

SQL 不能引用 blocked_columns；masked_labels 对结果标签执行额外遮盖。
不同入口共享同一 sanitize 函数。审计不存结果行、密钥、原始 SQL。
日志与异常不会主动输出底层驱动错误，避免携带连接字符串或查询字面量。
真实数据库账号的最小授权必须在数据库内配置，应用层校验不能替代它。
