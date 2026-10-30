# Agent 编排开发：Harness 执行循环与三条典型轨迹

> 阶段：提示词 7（Agent 编排开发）
> 代码：`src/admin/harness.py`（执行循环）、`src/admin/api.py`（`/api/agent/run`、`/api/agent/run/stream`、`/api/agent/confirm/{id}`）
> 数据：`agent_run_logs`（决策日志）、`agent_confirmations`（人工确认单）
> 红线不变：不自动锁档、不签约、不承诺容量；一切正式结论人工复核

---

## 一、框架选型：自研轻量 Harness

| 方案 | 优点 | 本场景的问题 | 结论 |
|---|---|---|---|
| LangChain AgentExecutor | 生态全、工具封装快 | 循环与中间件藏在抽象里，凭据校验/中断/日志要写一堆回调才能插进去；版本变动频繁 | 不采用 |
| LangGraph | 显式状态图、支持 human-in-the-loop 中断 | 对"最多 5 轮、6 个工具、单写操作"的规模属过度设计；引入新的图/检查点存储概念 | 二期工具扩容后再评估 |
| **自研 Harness（采用）** | 模型只负责"决策"，执行/校验/留痕/中断全部由代码掌握；零新增依赖；每个分支可单测 | 需要自己写循环（约 200 行） | ✅ **采用** |

**Harness 模式的职责切分**（这是能守住业务硬约束的关键）：

```
┌──────────────┐   decision (tool_calls / final)   ┌──────────────────────────────┐
│  模型（LLM）  │ ────────────────────────────────▶ │      Harness（代码，可信）     │
│ 只做决策      │ ◀──────────────────────────────── │ ① 工具白名单  ② 角色门禁       │
└──────────────┘        observation (结果回填)      │ ③ 凭据校验    ④ 强制中断       │
                                                    │ ⑤ 全量日志    ⑥ 轮数上限       │
                                                    └──────────────────────────────┘
```

模型永远拿不到"直接写库"的能力：`create_schedule_draft` 在 Harness 里被拦截，只有员工确认后
`resume()` 才会以 `allow_write=True` 调用真实实现。

---

## 二、执行循环

```
用户提问
  │
  ├─ 初始化 run_id（贯穿全部日志行）
  │
  └─ while round ≤ 5:                       ← 最多 5 轮，防失控
       ├─ 第 1 步：模型决策（mock 确定性规划器 / 真实 LLM tools 参数）
       ├─ 第 2 步：逐个执行工具
       │    ├─ 角色门禁 → 拒绝则记 denied 日志并回填"被拒绝"观察
       │    ├─ 只读工具（search_kb / get_schedule / check_*）→ 执行
       │    ├─ create_schedule_draft → ★ 强制中断（不执行）
       │    │     ├─ 创建 AgentConfirmation（status=pending）
       │    │     ├─ SSE 事件 pending_confirmation → 前端渲染确认卡片
       │    │     └─ 回合结束（等待员工操作）
       │    └─ 每次执行写 1 行 agent_run_logs（工具名/参数/耗时/状态/结果摘要）
       ├─ 第 3 步：结果回填（mock 直接进入下一步；LLM 追加 role=tool 消息）
       └─ 第 4 步：再决策（模型看到观察结果后决定下一步或收敛输出回答）
```

**中断—确认—恢复时序**：

```
员工/协调员        Harness                    工具层              数据库
    │  提交草稿参数    │                          │                   │
    │───────────────▶│ ①~④ 校验工具执行          │                   │
    │                │─────────────────────────▶│                   │
    │                │ ⑤ 命中 create → 中断      │                   │
    │                │── AgentConfirmation(pending) ─────────────────▶│
    │◀── SSE: pending_confirmation（草稿卡片）───│                   │
    │  点击"确认创建"  │                          │                   │
    │───────────────▶│ ⑥ 重新缺项校验/冲突校验    │                   │
    │                │─────────────────────────▶│                   │
    │                │ ⑦ create_schedule_draft（allow_write=True）    │
    │                │─────────────────────────▶│── ScheduleDraft ─▶│
    │                │ ⑧ submit_for_review → pending_review          │
    │◀── 最终回答+轨迹 │                          │                   │
```

**为什么恢复时要重新校验而不是复用中断前的 token**：
1. precheck_token 是 HMAC JWT，5 分钟过期，人工确认可能超时；
2. 更重要——确认等待窗口内**他人可能确认了冲突档期**，重新跑 `check_conflict` 能立刻捕获；
3. 复检失败则确认单直接置 rejected，草稿不落库。

---

## 三、决策日志表 `agent_run_logs`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | str(32) | 一次智能体回合的关联键（恢复阶段加后缀 `r`） |
| `session_id` / `message_id` | int | 关联会话与问答留痕（阶段 5 分析可按会话聚合） |
| `round_no` | int | 第几轮模型决策（1 起） |
| `step` | int | run 内全局步号（跨轮递增） |
| `tool_name` | str(64) | 工具名 |
| `arguments` | text | 入参 JSON（>1000 字符截断） |
| `duration_ms` | int | 执行耗时（毫秒） |
| `status` | str(24) | `ok` / `blocked`（校验阻断）/ `denied`（越权/跳步）/ `pending_confirm`（中断等待）/ `rejected`（员工取消）/ `confirmed` |
| `result_summary` | text | 结果摘要 JSON（≤500 字符） |
| `created_at` | datetime | 记录时间（索引） |

阶段 5 可直接据此出报表：工具调用频次与耗时分布、`denied` 跳步尝试率、中断到确认的等待时长、
各流程的平均轮数（判断是否经常撞到 5 轮上限）。

**实测日志样例**（流程 b + 确认恢复，`agent_run_logs` 实际行）：

```
6693ec7c5234  R1 S1 search_kb            522ms  ok              {"query":"帮我预定下个月上海场馆档期 …"}
6693ec7c5234  R2 S2 get_schedule          14ms  ok              {"venue":"副馆 Hall-B","date_range":"2026-12-25~2026-12-25"}
6693ec7c5234  R3 S3 check_missing_fields    0ms  ok              {"draft":{…}}
6693ec7c5234  R4 S4 check_conflict          4ms  ok              {"blocking":false,"conflicts":[…]}
6693ec7c5234  R5 S5 create_schedule_draft  94ms  pending_confirm {"confirmation_id":3}
6693ec7c5234r R1 S1 check_missing_fields    0ms  ok              （确认后复检）
6693ec7c5234r R1 S2 check_conflict          2ms  ok
6693ec7c5234r R1 S3 create_schedule_draft  79ms  ok              {"draft_id":4}
6693ec7c5234r R1 S4 submit_for_review      73ms  ok              {"status":"pending_review"}
```

---

## 四、三条典型对话的预期轨迹

### a. 「上海会展中心 3 天档期预订标准是什么」——纯口径咨询

**预期**：只查知识库，**1 轮收敛**，不触达任何后续工具。

| 轮 | 工具 | 状态 | 耗时 | 说明 |
|---|---|---|---|---|
| 1 | `search_kb` | ok | ~330ms | 混合检索 top3（BM25+向量 RRF） |

回答形态：
```
候选提示（依据知识库检索）：
- [来源1] 《场馆档期业务规范.md》v1/切片#0 与查询相关，请人工核对原文。
- [来源2] 《场馆档期业务规范.md》v1/切片#2 与查询相关，请人工核对原文。
- [来源3] 《场馆档期业务规范.md》v1/切片#3 与查询相关，请人工核对原文。

⚠️ 以上为系统候选提示，最终结论需要人工复核确认。
```
**判据**：轨迹长度 == 1，无 `check_*` / 无 `create_schedule_draft`。制度未命中时回答
"制度中未找到对应口径，请人工复核"（不允许编造规则）。

---

### b. 「帮我预定下个月上海场馆档期」——全流程预订（含人工确认）

**预期**：查询制度 → 查询档期空余 → 核验资料 → 冲突校验 → **生成草稿前中断** → 员工确认 → 创建 → 提交复核。

| 轮 | 工具 | 状态 | 耗时 | 说明 |
|---|---|---|---|---|
| 1 | `search_kb` | ok | 522ms | 预订标准 / 必填字段 / 冲突口径 |
| 2 | `get_schedule` | ok | 14ms | 副馆 Hall-B 在 12-25 前后的已确认快照 |
| 3 | `check_missing_fields` | ok | 0ms | 9 项必填齐备，签发 token_missing |
| 4 | `check_conflict` | ok | 4ms | 无硬冲突，签发 token_conflict |
| 5 | `create_schedule_draft` | **pending_confirm** | 94ms | ★ 中断，草稿内容推送员工确认 |

中断时前端收到的 SSE 事件：
```json
{"type":"pending_confirmation","confirmation_id":3,"draft":{"project_name":"智能仓储对接会",
 "venue":"副馆 Hall-B","event_start":"2026-12-25 09:00","event_end":"2026-12-25 16:00","dock_no":"D", …}}
```
员工点击「确认创建」后（`POST /api/agent/confirm/3 {"action":"confirm"}`）：

| 恢复步 | 工具 | 状态 | 说明 |
|---|---|---|---|
| 1 | `check_missing_fields` | ok | 复检（凭据重新签发） |
| 2 | `check_conflict` | ok | 复检（捕获等待窗口内档期变化） |
| 3 | `create_schedule_draft` | ok | 真正创建，draft_id=4 |
| 4 | `submit_for_review` | ok | 置 pending_review，交人工复核 |

最终回答：`✅ 草稿已创建（id=4，状态 draft，不生效）。已提交人工复核…`
**判据**：中断前**没有** `create_schedule_draft: ok`；员工取消则轨迹止于 `rejected`，草稿数为 0。

---

### c. 「这个档期预约能不能通过」——只核验不创建

**预期**：核验冲突与资料完整性 → 直接给结论，**2 轮**，绝不触达写工具。

| 轮 | 工具 | 状态 | 耗时 | 说明 |
|---|---|---|---|---|
| 1 | `check_missing_fields` | ok | 0ms | 9 项齐备 |
| 2 | `check_conflict` | ok / blocked | 2~40ms | 无硬冲突 → 候选"可通过初审"；有则将 `blocking` 置 true |

两种结论形态：
- 通过：`资料完整性：通过；冲突校验：无硬冲突 → 候选结论"可通过初审"，仍需人工复核确认。`
- 阻断：`[time_overlap] 与已确认档期 A001《春季设备展》时间窗重叠…口径：业务规范 1.2` +
  `[dock_conflict] 装卸口 B 被 A001 占用…口径：业务规范 2.2` → 建议人工处理。

**判据**：轨迹长度 == 2，`pending` 为空，`draft_id` 为空；硬冲突时第 2 步状态为 `blocked`
且**不会**产生确认单。

---

## 五、接口一览（新增部分）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/run` | 同步执行一回合，返回 answer / trace / pending |
| POST | `/api/agent/run/stream` | SSE 流式：`plan` → `round` → `tool` → `pending_confirmation` → `final` |
| POST | `/api/agent/confirm/{cid}` | 人工确认/取消，`action=confirm|reject`；确认后复检再创建 |
| GET | `/api/admin/audit/messages/{mid}/full` | 审计详情新增 `run_logs`（决策日志全量） |

**防滥用**：确认单绑定提交人（别人的确认单返回 404/409）、状态机只允许 `pending → confirmed/rejected`
（重复确认返回 409）、30 分钟超时自动作废、角色门禁在 Harness 与工具层各拦一道。

---

## 六、实测结果汇总

| 场景 | 结果 |
|---|---|
| 流程 a 口径咨询 | ✅ 1 轮 1 步，仅 `search_kb` |
| 流程 b 预订全流程 | ✅ 5 轮中断（pending_confirm #6），确认后复检+创建（draft_id=5）+提交复核 |
| 流程 b 员工取消 | ✅ 轨迹 `rejected`，草稿未创建 |
| 流程 c 核验 | ✅ 2 轮，结论"可通过初审"，无 pending |
| 硬冲突（与 A001 重叠 + 装卸口 B 占用） | ✅ 4 轮后 `blocked` 终止，输出两条冲突依据，**不产生确认单** |
| 一线员工触发创建 | ✅ 第 5 步 `denied`（"仅 coordinator/admin 可用"），无确认卡片 |
| 重复确认同一单 | ✅ 409 |
| 决策日志表 | ✅ 25 行，字段齐全（轮次/步号/耗时/状态/摘要） |
