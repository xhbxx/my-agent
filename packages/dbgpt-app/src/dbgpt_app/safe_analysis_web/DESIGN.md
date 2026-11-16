---
name: DATA-AGENT
description: 浅灰、紧凑、细分隔线的数据分析工作台局部视觉系统
colors:
  background: "#fff"
  sidebar: "#f7f7f8"
  surface: "#f8f8fa"
  text: "#252630"
  muted: "#626470"
  divider: "#e9e9ed"
  primary: "#5356c8"
  primary-soft: "#eeeef8"
  primary-hover: "#4447ab"
  navigation-dark: "#303445"
  error: "#b42318"
  success: "#16714a"
typography:
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "20px"
    fontWeight: 650
    lineHeight: 1.4
  title:
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.5
  question:
    fontSize: "18px"
    fontWeight: 600
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "14px"
    lineHeight: 1.65
  label:
    fontSize: "13px"
    fontWeight: 600
  helper:
    fontSize: "12px"
  sql:
    fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace'
    fontSize: "12px"
    lineHeight: 1.8
rounded:
  control: "6px"
  field: "8px"
  confirmation: "10px"
spacing:
  tight: "8px"
  compact: "12px"
  standard: "16px"
  panel: "20px"
  section: "24px"
  workspace: "28px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.background}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
  nav-active:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary}"
    rounded: "{rounded.control}"
    padding: "9px 10px"
  input:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text}"
    rounded: "{rounded.field}"
    padding: "9px 11px"
  sql-block:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.field}"
    padding: "18px"
    typography: "{typography.sql}"
---

# Design System: DATA-AGENT

## Overview

**Creative North Star: "Linear 风格的数据工作台"**

DATA-AGENT 使用用户选定的浅灰、紧凑、细分隔线方向，作为本模块自有产品品牌。正文、表格、SQL 和执行状态构成视觉中心，紫靛色强调操作及当前选择。这里记录 `assets/app.css` 与 `assets/index.html` 已实现的局部系统，不覆盖 DB-GPT 仓库其他页面的视觉规范。

底层框架仍为 DB-GPT；包名、命令、框架归属和版权保留。Linear 仅描述选定风格，不代表官方设计系统或关联产品。品牌使用 DATA-AGENT 文字及三条高度不同的线条组成的 SVG 数据标记。

**Key Characteristics:**
- 浅灰导航、白色工作面与细分隔线。
- 紧凑中文工具界面，表格与 SQL 优先。
- 紫靛色集中表达操作、选中与焦点。
- 平面层级与小圆角，不依赖阴影。

## Colors

色值以 frontmatter 为准，CSS 自定义属性是实现来源；下文描述使用角色。

### Primary

- **工作紫靛**（primary，对应 `--blue`）：主按钮、当前页签、阶段、复选框与柱图。
- **浅紫靛底**（primary-soft，对应 `--blue-soft`）：当前导航项背景。
- **深紫靛交互**（primary-hover）：主按钮悬停。

### Neutral

- **工作白**（background）：工作区、字段和次按钮。
- **导航浅灰**（sidebar）：固定侧栏。
- **代码浅灰**（surface）：SQL、行内代码和次按钮悬停。
- **正文墨灰**（text）、**辅助灰**（muted）、**分隔灰**（divider）：内容、解释与结构边界。
- **导航深墨**（navigation-dark）：品牌图形底与新建分析按钮。

错误使用 error，完成阶段使用 success；确认与通知保留 CSS 中的暖色语义表面。

**The State Color Rule.** 选中和焦点使用工作紫靛；成功、错误与风险提示沿用各自语义，并保留文字说明。

## Typography

正文使用 frontmatter 的系统界面字体栈，SQL 使用独立等宽栈。没有独立展示字体或营销大标题角色。

### Hierarchy

- **Headline**：工作台页标题；窄屏由媒体查询缩小为（19px）。
- **Title**：结果与空状态小标题。
- **Question**：提问表单的主要标签。
- **Body**：正文与通用交互控件。
- **Label / Helper**：面板标题、字段信息、状态与辅助说明。
- **SQL**：可换行代码块；表格与图表数字使用等宽数字特性。

连接页标题局部使用（24px），窄屏为（21px）；不扩展为全局展示字阶。Markdown 阅读区最大宽度为（75ch）。

## Layout

桌面固定侧栏宽（196px），工作区用相同左边距让位。分析区采用（240px）数据范围列与可收缩主列；主内容最大宽（1160px），水平内边距为 `clamp(20px,3vw,44px)`。顶部栏、执行阶段、页签与结果以细线串联。

连接表单宽度为 `min(560px,calc(100% - 64px))`。布局节奏采用 frontmatter 中实际复用的间距，具体表面保留 CSS 中的局部数值。

- （1200px）及以下：侧栏缩至（186px），数据列缩至（220px），主区边距收紧。
- （900px）及以下：侧栏默认收起，顶栏显示“菜单”按钮以展开原侧栏，保留新建分析、连接后的断开连接与全部导航入口；数据列为（210px）。
- （680px）及以下：数据区与主区上下堆叠，隐藏表字段和指标明细，保留数据源及检索入口；连接控件纵向排列，提交操作占独立一行。

表格横向滚动，最高（360px）；SQL 最高（340px），允许滚动与长内容换行。图表由横向滚动容器承载；（680px）及以下 SVG 最小宽（640px），展示左右滑动说明，标签保持（12px）。

**The Data Readability Rule.** 窄屏以横向滚动保留图表标签与数据可读性，不能用整体缩放把文字压小。

## Elevation & Depth

当前实现没有 box-shadow。侧栏、工作面、代码浅底、单像素边界与间距负责层次。风险确认是暖色背景与边框的内嵌面板。

**The Flat Workbench Rule.** 延续平面工作区，用背景、分隔线和留白建立层级。

## Shapes

按钮与导航使用 control 小圆角，输入、SQL 与表格容器使用 field 圆角，风险确认使用 confirmation 圆角。边框通常为（1px），页签以底边标示选中。品牌 SVG 使用紧凑方形底与线性数据图形；状态点为圆形。

## Components

### Buttons

主按钮为紫靛底白字，次按钮为白底描边，新建分析为深墨底。尺寸与圆角见 frontmatter。主、次按钮过渡为背景和文字色（150ms），使用 CSS 默认 ease；禁用透明度为（0.5）并使用不可用指针。

所有交互元素的键盘焦点为（2px）紫靛轮廓与（3px）偏移。保留原生按钮及明确动作文字。

### Inputs / Fields

令牌和下拉框为细描边白底；提问框内边距（14px 16px），可纵向调整，高度范围为（100px–300px）。焦点使用统一轮廓，错误反馈在字段下方独立展示。

### Navigation

导航使用线性 SVG、文字与紧凑行间距；当前项使用浅紫靛底、紫靛文字与加重字。悬停用浅灰底。窄屏通过顶栏“菜单”按钮开合原侧栏：展开后保留新建、断开连接及导航可达，按钮通过 `aria-controls` 关联导航并同步 `aria-expanded` 与“关闭菜单”文字。Escape、新建分析、断开连接都会关闭菜单。侧栏展开时以细边框与前景层级区分，不新增阴影。结果页签为无背景的下划线导航，选中状态对应 `aria-selected`。

### Containers

普通区段保持平面。表格容器有细边框与滚动区；表头固定并使用浅灰底，行悬停使用更浅灰色。风险确认面板使用暖色语义面，操作位于右下方并在窄屏换行。

### Analysis Results

SQL、表格、SVG 柱图与 Markdown 是同一分析结果的不同表达。SQL 保留等宽格式与滚动，柱图沿用主强调色并遵循数据可读性规则，Markdown 链接保留紫靛色和下划线。检索依据逐条分隔；阶段同时用文字和状态颜色表达。

## Do's and Don'ts

### Do:

- **Do** 延续浅灰侧栏、白色工作面、细分隔线与紧凑间距。
- **Do** 使用 DATA-AGENT 文字品牌和本模块 SVG 数据标记。
- **Do** 保留明确的焦点、状态文字、表格和图表滚动与 SQL 换行。
- **Do** 将此规范限定在 safe_analysis_web，准确表达自有产品身份与 DB-GPT 框架归属。

### Don't:

- **Don't** 把旧 DB-GPT 中文 logo 作为本模块产品标识恢复。
- **Don't** 将风险色用于普通主操作或当前导航。
- **Don't** 把本模块局部规则推广成整个仓库的规范。
