# 2 · MVP 项目（老师清单 src-2）

**代码实体位于 [`src/admin/`](../admin/)，可直接运行**（`cd src/admin && DB_PATH=../../data/venue_scheduling.db python -m uvicorn main:app --port 8500`，账号见文末）。本文件按老师要求的五个要点逐项给出架构说明与代码位置；量化验证见 `Test/00-场景测试方案`（26 条接口用例 + 18 条单元测试，100% PASS）。

## 一、工程架构

分层架构，服务端渲染 + REST API 双通道：

```
浏览器 ── Jinja2 SSR 页面（pages.py + templates/）
       └─ REST API（api.py，26 个接口，前缀 /api）
              │
        auth.py ── JWT（Bearer + HttpOnly Cookie 双通道）+ 接口级 RBAC 依赖注入
              │
        harness.py ── Agent 执行循环（见下）
              │
        tools.py ── 6 个工具（唯一业务操作入口）
              │
        rag.py ── 切片 / 向量化 / FAISS+关键词混合检索 / LLM 多供应商适配
              │
        database.py ── SQLAlchemy ORM，10 张表 → SQLite（data/venue_scheduling.db）
```

| 模块 | 代码 | 职责 |
|------|------|------|
| 入口 | `src/admin/main.py` | FastAPI 装配、启动建表、索引加载 |
| 页面层 | `src/admin/pages.py` + `templates/`（10 个页面） | 登录/问答/档期/知识库/提示词/审计/用户管理 |
| 接口层 | `src/admin/api.py` | 26 个 REST 接口，全部经依赖注入做角色校验 |
| 认证 | `src/admin/auth.py` | JWT 签发校验、密码哈希、`require_login` / `require_admin` |
| Agent | `src/admin/harness.py` | 决策循环、工具调度、强制中断、日志留痕 |
| 工具 | `src/admin/tools.py` | 6 个工具规范 + 实现 + 业务校验 |
| RAG | `src/admin/rag.py` | 切片、嵌入、FAISS 索引、混合检索、LLM 适配（mock 离线可跑） |
| 数据 | `src/admin/database.py` | 10 张表：users / chat_sessions / chat_messages / kb_documents / kb_chunks / prompt_templates / schedule_slots / schedule_drafts / confirmations / agent_run_logs |
| 导出 | `src/admin/export.py` | 草稿导出 Markdown / ICS、审计统计 |

## 二、Agent 框架

`src/admin/harness.py` — 模型决策 → 执行工具 → 结果回填 → 再决策，**最多 5 轮**：

- **工具规范 6 个**（`tools.py:54-92`）：`search_kb`（知识库检索）、`get_schedule`（档期查询）、`check_missing_fields`（缺项校验）、`check_conflict`（冲突校验）、`create_schedule_draft`（创建草稿，**写操作唯一入口，强制中断**）、`submit_for_review`（提交复核）
- 每次工具执行写 `agent_run_logs`（轮次/工具/参数/耗时/状态/结果摘要），65 条真实轨迹见 [`src/1-问答助手归档/agent_run_logs.json`](../1-问答助手归档/agent_run_logs.json)
- 接口：`POST /api/agent/run`、`POST /api/agent/run/stream`（SSE 流式）、`POST /api/agent/confirm/{cid}`（人工确认恢复）
- 安全设计：`allow_write` 开关使循环内**没有任何代码路径能绕过确认**直接执行写工具（harness.py:117-119）；对抗用例 A1~A4（提示注入/越权调用）实测全部被拦截，见 `Test/04-对抗场景测试.docx`

## 三、知识库（RAG）

- 表：`kb_documents`（文档，含版本号）/ `kb_chunks`（切片，含向量）——当前 **6 篇文档 / 25 个切片**，含《场馆档期业务规范》等
- 检索：`rag.py hybrid_search` — FAISS 向量召回 + 关键词匹配混合排序 top3，召回片段随每轮问答留痕（含 doc_version，可追溯）
- 管理：管理员页 `/admin/kb` 上传/删除文档（自动切片+向量化），`/admin/prompt` 编辑生效中的系统提示词模板（版本化）
- 实测命中示例：提问"装卸口冲突怎么判定"命中《场馆档期业务规范》切片#2，见 `src/1-问答助手归档/对话归档.md`

## 四、人工确认/审核点（≥3 处，实际 4 处）

| # | 确认点 | 代码位置 | 行为 |
|---|--------|----------|------|
| ① | **S4 角色门禁** | `harness.py:176-181` | 员工（staff）触发创建草稿直接拒绝："您的角色只能查询与核验"，不出现确认卡片 |
| ② | **创建草稿强制中断** | `harness.py:137 / 183-190`；确认接口 `api.py:376` | 写工具一律不直接执行：先落 `pending_confirm` 日志 + 生成确认单，SSE 推送草稿内容，**人工点"确认"后才落库**；恢复时重跑缺项/冲突校验（捕获等待窗口内的档期变化） |
| ③ | **提交复核工作流** | `tools.py:276-289`（`submit_for_review`） | 草稿 `draft → pending_review`，最终结论需人工确认后才生效 |
| ④ | **渲染层强制复核脚注** | `harness.py:48`（`FIXED_FOOTER`） | 所有业务结论尾部强制追加"⚠️ 以上为系统候选提示，最终结论需要人工复核确认"，不受提示词模板编辑影响 |

实测证据：确认中断→人工确认→恢复执行全链路见 `Test/01-正常场景测试.docx`（N3/N5 用例，含确认单截图）；员工越权被拒见 `Test/04-权限场景测试.docx`。

## 五、权限体系（≥2 个角色，实际 3 个）

| 角色 | 权限 | 实测 |
|------|------|------|
| `admin` 管理员 | 全部接口：知识库/提示词/用户管理/审计 + 业务全流程 | — |
| `coordinator` 协调员 | 业务流程（S4 允许角色：创建草稿、提交复核） | — |
| `staff` 一线员工 | 仅查询与核验；创建草稿被①拦截；管理接口 403 | `GET /api/admin/users → 403`（归档自 2026-11-06 实测） |

实现方式：**接口级 RBAC 依赖注入**——`require_admin` ×16 个接口、`require_login` ×12 个接口（`src/admin/auth.py:112`，`api.py` 全部路由），而非仅前端隐藏。认证双通道：`Authorization: Bearer` 或 HttpOnly Cookie 任一有效即放行（适配网关/浏览器策略）。

## 演示账号

`admin/admin123`（管理员）· `coord/coord123`（协调员）· `staff/staff123`（一线员工）
线上地址与验证记录见 `Test/06-上线部署网站地址.md`。
