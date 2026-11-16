# DATA-AGENT 数据分析工作台维护说明

DATA-AGENT 是基于 DB-GPT 框架的自有数据分析工作台，入口为 `/safe-analysis/`。页面采用用户选定的 Linear 风格：浅灰、紧凑、细分隔线。桌面布局为左侧数据范围、右侧问答与结果，使用中文浅色界面；启动、令牌配置、示例数据库与接口调用见 [安全分析运行与阅读指南](../../../../../learn/SAFE_ANALYSIS_GUIDE_CN.md)。

## 模块职责

| 文件 | 职责 |
| --- | --- |
| `routes.py` | 同源页面和静态资源路由、资源白名单与 CSP；启用安全分析配置后首页跳转到本页，`/?classic=1` 访问原版首页。 |
| `assets/index.html` | 导航、连接表单、数据范围、提问、执行确认和结果页签的语义结构。 |
| `assets/app.css` | 页面配色、字体、布局、交互状态和窄屏适配。 |
| `assets/app.js` | 连接与会话状态、提交与停止、确认执行、页签和事件反馈。 |
| `assets/api.js` | Bearer 认证、同源 HTTP 调用、流式读取和取消请求。 |
| `assets/stream.js` | 跨网络分块的 SSE 事件解析。 |
| `assets/render.js` | 授权表结构、指标、检索证据、结果表格、SVG 图表、Markdown 和 CSV 导出。 |
| `assets/marked.umd.js`、`assets/purify.min.js` | 本地 Markdown 解析与 HTML 净化依赖；对应许可证随资源保存。 |

## 产品品牌与局部视觉系统

本页面以 DATA-AGENT 为产品品牌，使用内联 SVG 数据标记与本地 `assets/favicon.svg`，不再复用 `/logo_zh_latest.png`。底层框架仍为 DB-GPT，包名、运行命令、框架归属与版权保留；新自有环境变量采用 `DATA_AGENT_*` 命名。通过同源链接进入原数据源、知识库、Skills 与原版对话页面。

本页是独立静态实现，没有直接加载原 React/Ant Design 组件。局部样式以 `assets/app.css` 为准：正文 14px，页标题 20px，普通标题 16px，辅助文字 12–13px；主强调为紫靛色 `#5356c8`，通过细分隔线和浅底色组织层次，不使用阴影；按钮圆角 6px，字段与表格 8px，确认面板 10px。窄屏规则在 1200px、900px、680px 断点调整布局，移动图表用横向滚动保留 12px 标签与 640px 最小图宽。

[PRODUCT.md](PRODUCT.md) 记录产品功能、已选方向、演示边界与依赖归属；[DESIGN.md](DESIGN.md) 记录实际复用 token、布局与组件规则，[局部 sidecar](.impeccable/design.json) 补充焦点、动效、断点和自包含组件预览。视觉规范仅约束本模块，不替换仓库框架视觉规范。

900px 及以下通过顶栏“菜单”按钮展开原侧栏，保留“新建分析”、连接后的“断开连接”及全部导航入口。按钮同步展开状态，支持 Escape 关闭；新建分析或断开连接后也会关闭菜单。

## 真实接口与数据

接口定义见 `../openapi/api_v1/safe_analysis_api.py`，前端统一调用 `/api/v1/safe-analysis` 下的真实接口：

- `GET /sources`：当前令牌授权的数据源、可见表字段、指标与检索状态。
- `POST /context`：当前问题的实际检索证据。
- `POST /query`：分析请求，通过 SSE 返回阶段、SQL、结果、解释、风险确认或错误。
- `POST /requests/{request_id}/cancel`：请求服务端取消执行。

表格、图表、SQL 与证据都来自接口返回，没有浏览器端伪造的成功结果。快捷问题只填入问题文本；“查看一条示例记录”会向 `sales_demo` 提交真实 SQL。指南中的演示数据库包含合成数据，不能当作生产业务数据。令牌保存在当前浏览器会话的 `sessionStorage`，断开连接时清除。

## 核验边界

本轮已在真实浏览器验证连接、示例 SQL、Qdrant 检索、执行确认，以及表格、图表和 Markdown 结果展示。验证使用固定测试模型与合成数据；不构成真实供应商模型 API 的联调证明，也不代表生产业务数据验证。

视觉系统文档以当前 CSS 与页面结构为依据。上线前仍需针对实际部署环境检查授权范围、模型配置与真实业务数据，并对目标设备上的窄屏排版和异常状态进行验收。
