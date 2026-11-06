# -*- coding: utf-8 -*-
"""Agent Harness 执行循环（提示词 7 · 阶段 4：从固定流程升级为受控自主决策）。

【框架选型：自研轻量 Harness（Harness 模式），不引入 LangChain / LangGraph】
理由（结合本系统的业务硬约束）：
  1. 本系统的核心风险控制（precheck_token 凭据、角色门禁、工具白名单、全量审计）
     必须落在**代码层**，而重框架把循环藏在抽象里，恰恰弱化了对"每一步"的掌控；
  2. 工具只有 6 个、单轮最大 5 步，Harness 模式（模型只负责"决策"，Harness 负责
     "执行 + 校验 + 留痕 + 中断"）完全够用，零新增依赖、可测试、可解释；
  3. 与 docs/09 的三层防线无缝衔接：Harness 是第①层（代码级）的执行载体。
后续若工具规模扩大，可平滑替换为 LangGraph（决策/执行分离的接口已按此设计）。

【执行循环】
    模型决策 → 执行工具 → 结果回填 → 再决策 → …（最多 MAX_ROUNDS 轮）
  - mock 通道：确定性规划器按三种典型流程产出决策（离线可复现，供演示/回归）；
  - 真实 LLM 通道：OpenAI 兼容 tools 参数，模型自主决策，Harness 逐步回填 tool 消息。

【两条不可协商的 Harness 级规则】
  1. 每一轮决策与每一次工具执行（含被拒绝的）都写入 agent_run_logs 日志表；
  2. create_schedule_draft 执行前**强制中断**：草稿内容流式推送给员工确认
     （SSE 事件 pending_confirmation），确认前草稿绝不落库；恢复执行时对草稿
     重新跑缺项/冲突校验（捕获等待窗口内档期变化），通过后才真正创建。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import uuid

from sqlalchemy.orm import Session as DbSession

from database import AgentConfirmation, AgentRunLog, ChatMessage, ChatSession, PromptTemplate, now
from rag import call_llm, get_llm_config
from tools import (
    S4_ALLOWED_ROLES, TOOLS_SPEC, ToolDenied, ToolError,
    check_conflict_impl, check_missing_fields_impl, draft_hash,
    t_create_schedule_draft_impl, t_get_schedule, t_search_kb, t_submit_for_review_impl,
)

# 单回合模型决策的最大轮数（提示词 7 第 1 项要求：最多 5 轮）
MAX_ROUNDS = 5

# 中断等待人工确认的最长时限（超时视为放弃，确认接口将拒绝恢复）
CONFIRM_EXPIRE_MINUTES = 30

FIXED_FOOTER = "⚠️ 以上为系统候选提示，最终结论需要人工复核确认。"


class _Interrupt(Exception):
    """Harness 内部控制流：捕获到 create_schedule_draft 调用，需要中断等待人工确认。"""

    def __init__(self, draft: dict):
        self.draft = draft


class AgentHarness:
    """一次智能体回合的执行器：决策循环 + 日志留痕 + 强制中断。"""

    def __init__(self, user, db: DbSession, session_id: int | None = None):
        self.user = user
        self.db = db
        self.session_id = session_id
        self.run_id = uuid.uuid4().hex[:12]   # 贯穿本回合所有日志行的关联键
        self.trace: list[dict] = []           # 返回给前端/审计页的轨迹（与日志表同构）
        self._step = 0                        # run 内全局步号
        self._round = 0                       # 模型决策轮号
        self._tokens: dict[str, str] = {}     # 凭据缓存（键 = 草稿哈希:类型）
        self._on_event = None                 # SSE 事件回调（流式推送用）
        self._message_id: int | None = None    # 留痕消息 id（回合结束后回填日志行）

    # ------------------------------------------------------------------
    # 日志：既写数据库（agent_run_logs），也写内存 trace（返回前端/审计）
    # ------------------------------------------------------------------

    def _log(self, tool: str, args: dict, status: str, result, started: float | None = None):
        self._step += 1
        duration_ms = int((time.perf_counter() - started) * 1000) if started else 0
        # 结果摘要：JSON 序列化后截断到 500 字符，保证日志表不会撑爆
        summary = json.dumps(result, ensure_ascii=False, default=str)
        if len(summary) > 500:
            summary = summary[:500] + "…(截断)"
        args_json = json.dumps(args, ensure_ascii=False, default=str)
        if len(args_json) > 1000:
            args_json = args_json[:1000] + "…(截断)"
        # 1) 数据库日志行（阶段 5 分析用）
        self.db.add(AgentRunLog(
            run_id=self.run_id, session_id=self.session_id, message_id=self._message_id,
            round_no=self._round, step=self._step, tool_name=tool,
            arguments=args_json, duration_ms=duration_ms,
            status=status, result_summary=summary,
        ))
        self.db.commit()
        # 2) 内存 trace（同构简化版）
        self.trace.append({"step": self._step, "round": self._round, "tool": tool,
                           "status": status, "args": args, "result": result,
                           "duration_ms": duration_ms})
        # 3) SSE 流式事件（前端逐步渲染）
        self._emit({"type": "tool", "step": self._step, "round": self._round,
                    "tool": tool, "status": status, "duration_ms": duration_ms})

    def _emit(self, event: dict):
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:   # 回调异常绝不影响主流程
                pass

    # ------------------------------------------------------------------
    # 工具执行器：统一异常转换 + 凭据缓存
    # ------------------------------------------------------------------

    def _execute(self, tool: str, args: dict, allow_write: bool = False) -> dict:
        """执行单个工具。

        allow_write=False（默认）：create_schedule_draft 一律抛 _Interrupt，交由人工确认；
        allow_write=True：仅由 resume() 在"员工已确认"之后传入，才真正调用写工具。
        这是"写操作唯一入口"的代码级实现——循环里没有任何路径能绕过确认。
        """
        started = time.perf_counter()
        try:
            if tool == "search_kb":
                result = t_search_kb(args["query"], self.db, int(args.get("top_k", 3)))
            elif tool == "get_schedule":
                result = t_get_schedule(self.db, args.get("venue", ""),
                                        args.get("date_range", ""), args.get("request_no", ""))
            elif tool == "check_missing_fields":
                result = check_missing_fields_impl(args["draft"])
                if result.get("precheck_token"):
                    self._tokens[result["draft_hash"] + ":missing"] = result["precheck_token"]
            elif tool == "check_conflict":
                result = check_conflict_impl(args["draft"], self.db)
                if result.get("precheck_token"):
                    self._tokens[result["draft_hash"] + ":conflict"] = result["precheck_token"]
            elif tool == "create_schedule_draft":
                if not allow_write:            # ★ Harness 级强制中断：写工具绝不直接执行
                    raise _Interrupt(args["draft"])
                result = t_create_schedule_draft_impl(
                    args["draft"], args.get("precheck_token_missing"),
                    args.get("precheck_token_conflict"), self.user.id,
                    self.user.role, self.user.username, self.db)
            elif tool == "submit_for_review":
                result = t_submit_for_review_impl(int(args["draft_id"]),
                                                  self.user.role, self.user.username, self.db)
            else:
                result = {"denied": True, "error": f"未知工具 {tool}（不在白名单）"}
        except (ToolDenied, ToolError) as e:
            result = {"denied": True, "error": str(e)}
        # 统一记日志（此处不记 create_schedule_draft——它在调用点以 pending_confirm 记录）
        status = ("denied" if result.get("denied")
                  else "blocked" if result.get("blocking")
                  else "ok")
        self._log(tool, args, status, result, started)
        return result

    # ------------------------------------------------------------------
    # 主循环（mock 通道）：模型决策 → 执行 → 回填 → 再决策，最多 5 轮
    # ------------------------------------------------------------------

    def run_mock(self, question: str, draft: dict | None) -> dict:
        # ---- 确定性规划器：按提问意图选择三种典型流程之一（见 docs/10 轨迹示例）----
        plan = self._mock_plan(question, draft)
        self._emit({"type": "plan", "plan": plan["label"]})
        cursor = 0
        answer, pending = None, None
        while cursor < len(plan["steps"]) and self._round < MAX_ROUNDS:
            tool, args = plan["steps"][cursor]
            if tool == "_answer":          # 收敛步：不算一轮"模型决策"，直接产出回答
                answer = args["text"]()    # "text" 是已绑定的回答组装器方法
                break
            self._round += 1
            self._emit({"type": "round", "round": self._round})
            cursor += 1
            if tool == "create_schedule_draft":
                # 强制中断点①：先做角色门禁（员工看不到确认卡片，直接拒绝）
                if self.user.role not in S4_ALLOWED_ROLES:
                    self._log("create_schedule_draft", {"draft": draft or args.get("draft")}, "denied",
                              {"error": f"S4 草稿创建试点期仅 {'/'.join(S4_ALLOWED_ROLES)} 可用"})
                    answer = (f"创建草稿技能试点期仅 {'/'.join(S4_ALLOWED_ROLES)} 可用，"
                              "您的角色只能查询与核验；如确需提交档期，请联系项目流程协调员。\n\n" + FIXED_FOOTER)
                    break
                # 强制中断点②：不执行，先落"待确认"日志 + 创建确认单
                started = time.perf_counter()
                pending = self._interrupt_for_confirmation(question, draft or args.get("draft"))
                self._log("create_schedule_draft", {"draft": pending["draft"]},
                          "pending_confirm", {"notice": "已中断，等待员工确认后才会创建草稿",
                                              "confirmation_id": pending["confirmation_id"]},
                          started)
                answer = pending["answer"]
                break
            # 普通工具：执行 + 日志；若校验未通过，按业务硬约束立即终止（不再进入中断）
            r = self._execute(tool, args)
            if tool == "check_missing_fields" and not r.get("complete"):
                answer = ("缺项校验未通过，流程终止，请补齐后重试：\n"
                          + "\n".join(f"- {m}" for m in r.get("missing", []))
                          + "\n\n" + FIXED_FOOTER)
                break
            if tool == "check_conflict" and r.get("blocking"):
                answer = ("冲突校验发现硬冲突（blocking），按风控规则禁止创建草稿，转人工处理：\n"
                          + "\n".join(f"- [{c['type']}] {c['evidence']}"
                                      for c in r.get("conflicts", []) if c.get("blocking"))
                          + "\n\n" + FIXED_FOOTER)
                break
        if answer is None:                 # 达到 5 轮上限仍未收敛 → 强制终止
            answer = (f"已达到单次任务最大轮数（{MAX_ROUNDS}），为防止失控终止执行；"
                      "请查看工具调用轨迹后人工介入。")
        return {"answer": answer, "pending": pending}

    # ------------------------------------------------------------------
    # 主循环（真实 LLM 通道）：tools 参数自主决策，Harness 逐步回填
    # ------------------------------------------------------------------

    def run_llm(self, question: str, draft: dict | None, cfg: dict) -> dict:
        import requests

        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        from agent import AGENT_SYSTEM_PROMPT   # 复用既有系统提示词（三层防线第③层）
        messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        pending, answer, draft_id = None, None, None
        while self._round < MAX_ROUNDS:
            self._round += 1
            self._emit({"type": "round", "round": self._round})
            resp = requests.post(url, headers=headers, timeout=120,
                                 json={"model": cfg["model"], "messages": messages,
                                       "tools": TOOLS_SPEC, "temperature": 0.2})
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")
            if not tool_calls:             # 模型给出最终回答 → 循环收敛
                answer = msg.get("content", "")
                break
            messages.append(msg)
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "create_schedule_draft":
                    # 角色门禁先在 Harness 层拦一道（工具层还会再校验，双保险）
                    if self.user.role not in S4_ALLOWED_ROLES:
                        denied = {"denied": True,
                                  "error": f"S4 草稿创建试点期仅 {'/'.join(S4_ALLOWED_ROLES)} 可用"}
                        self._log(name, args, "denied", denied)
                        messages.append({"role": "tool", "tool_call_id": tc["id"],
                                         "content": json.dumps(denied, ensure_ascii=False)})
                        continue
                    # ★ 强制中断：向模型回填"等待确认"观察，终止本回合
                    started = time.perf_counter()
                    pending = self._interrupt_for_confirmation(question, args.get("draft") or draft or {})
                    self._log("create_schedule_draft", {"draft": pending["draft"]},
                              "pending_confirm", {"confirmation_id": pending["confirmation_id"]}, started)
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": "INTERRUPTED: create_schedule_draft 已被 Harness 拦截，"
                                                "草稿内容已发送给员工确认，确认通过后才会执行。"})
                    answer = pending["answer"]
                    break
                result = self._execute(name, args)
                if name == "create_schedule_draft" and not result.get("denied"):
                    draft_id = result.get("draft_id")
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            if pending:
                break
        if answer is None:
            answer = (f"已达到单次任务最大轮数（{MAX_ROUNDS}），为防止失控终止执行；"
                      "请查看工具调用轨迹后人工介入。")
        return {"answer": answer, "pending": pending, "draft_id": draft_id}

    # ------------------------------------------------------------------
    # 强制中断：创建确认单 + 流式推送草稿卡片给员工
    # ------------------------------------------------------------------

    def _interrupt_for_confirmation(self, question: str, draft: dict) -> dict:
        draft = {k: str(draft.get(k, "")).strip() for k in (
            "project_name", "organizer", "venue", "event_start", "event_end",
            "dock_no", "load_in_start", "load_out_end")}
        rec = AgentConfirmation(run_id=self.run_id, user_id=self.user.id,
                                username=self.user.username, session_id=self.session_id,
                                draft=json.dumps(draft, ensure_ascii=False), question=question)
        self.db.add(rec)
        self.db.commit()
        # SSE：草稿内容此刻即流式推送给前端，渲染为确认卡片（流式第①推）
        self._emit({"type": "pending_confirmation", "confirmation_id": rec.id, "draft": draft})
        answer = (
            "🛑 **已按风控要求中断：创建档期草稿前需要您确认。**\n\n"
            "拟创建的草稿内容如下（确认后才会执行创建，且草稿不生效、需人工复核）：\n"
            + "\n".join(f"- {k}: {v}" for k, v in draft.items() if v)
            + f"\n\n确认单号 #{rec.id}（{CONFIRM_EXPIRE_MINUTES} 分钟内有效）。"
              "确认前系统将重新校验缺项与冲突（防止等待期间档期变化）。"
        )
        return {"confirmation_id": rec.id, "draft": draft, "answer": answer}

    # ------------------------------------------------------------------
    # 确认恢复：员工点击确认后从这里继续（重新校验 → 创建 → 提交复核）
    # ------------------------------------------------------------------

    def resume(self, rec: AgentConfirmation, action: str, decided_by: str) -> dict:
        draft = json.loads(rec.draft)
        lines: list[str] = []
        self._round = 1                    # 恢复阶段同样按"轮"记日志（复检属于第 1 轮决策）
        if action != "confirm":
            rec.status, rec.decided_by, rec.decided_at, rec.reject_reason = "rejected", decided_by, now(), "员工取消"
            self.db.commit()
            self._log("create_schedule_draft", {"draft": draft, "action": action},
                      "rejected", {"notice": "员工取消，草稿未创建"})
            lines.append("已按您的操作取消，草稿未创建。")
            return {"answer": "\n".join(lines), "draft_id": None}
        rec.status, rec.decided_by, rec.decided_at = "confirmed", decided_by, now()
        self.db.commit()
        # 恢复步骤 1/3：重新缺项校验（等待窗口内凭据可能已过期，重跑保证新鲜）
        r1 = self._execute("check_missing_fields", {"draft": draft})
        if not r1.get("complete"):
            rec.status, rec.reject_reason = "rejected", f"确认后复检缺项未通过：{r1.get('missing')}"
            self.db.commit()
            lines.append(f"确认后复检发现缺项（{r1.get('missing')}），草稿未创建，请补齐后重新发起。")
            return {"answer": "\n".join(lines), "draft_id": None}
        # 恢复步骤 2/3：重新冲突校验——若等待期间出现新的已确认档期，此处会重新检出
        r2 = self._execute("check_conflict", {"draft": draft})
        if r2.get("blocking"):
            rec.status, rec.reject_reason = "rejected", "确认后复检发现硬冲突"
            self.db.commit()
            lines.append("确认后复检发现硬冲突，草稿未创建（等待确认期间档期可能已变化）：")
            lines += [f"- [{c['type']}] {c['evidence']}" for c in r2.get("conflicts", []) if c.get("blocking")]
            lines.append("建议人工处理。")
            return {"answer": "\n".join(lines), "draft_id": None}
        # 恢复步骤 3/3：凭据复检通过 → 真正创建（allow_write=True 仅在确认后传入）
        tk_m = self._tokens.get(r1["draft_hash"] + ":missing")
        tk_c = self._tokens.get(r2["draft_hash"] + ":conflict")
        r3 = self._execute("create_schedule_draft",
                           {"draft": draft, "precheck_token_missing": tk_m,
                            "precheck_token_conflict": tk_c},
                           allow_write=True)
        if r3.get("denied"):
            rec.status, rec.reject_reason = "rejected", f"创建被服务端拒绝：{r3.get('error')}"
            self.db.commit()
            lines.append(f"创建被服务端拒绝：{r3.get('error')}")
            return {"answer": "\n".join(lines), "draft_id": None}
        rec.draft_id = r3["draft_id"]
        self.db.commit()
        for c in r2.get("conflicts", []):
            lines.append(f"非阻断提示：[{c['type']}] {c['evidence']}")
        lines.append(f"✅ 草稿已创建（id={r3['draft_id']}，状态 draft，不生效）。")
        # 自动进入人工复核队列（智能体能触达的最后一个状态）
        r4 = self._execute("submit_for_review", {"draft_id": r3["draft_id"]})
        lines.append(f"{r4.get('notice')}")
        lines.append("")
        lines.append(FIXED_FOOTER)
        return {"answer": "\n".join(lines), "draft_id": r3["draft_id"]}

    # ------------------------------------------------------------------
    # mock 确定性规划器：三种典型流程（对应 docs/10 的轨迹示例 a/b/c）
    # ------------------------------------------------------------------

    def _mock_plan(self, question: str, draft: dict | None) -> dict:
        # 流程 c：核验类提问（"这个档期预约能不能通过"）→ 只做校验，绝不创建
        if draft and re.search(r"能不能|能否|是否可以|能不能通过|核验|校验|检查.*通过", question):
            return {"label": "核验流程（缺项校验 → 冲突校验 → 结论）", "steps": [
                ("check_missing_fields", {"draft": draft}),
                ("check_conflict", {"draft": draft}),
                ("_answer", {"text": self._answer_verify}),
            ]}
        # 流程 b：预订类提问（带草稿参数）→ 全流程，最后一步是强制中断点
        if draft and re.search(r"预定|预订|预约|创建|申请|排.*档", question):
            date_range = "~".join(filter(None, [draft.get("event_start", "")[:10],
                                                draft.get("event_end", "")[:10]]))
            return {"label": "预订流程（制度 → 档期 → 缺项 → 冲突 → 草稿[待确认]）", "steps": [
                ("search_kb", {"query": f"{question} 预订标准 必填字段 冲突判定口径"}),
                ("get_schedule", {"venue": draft.get("venue", ""), "date_range": date_range}),
                ("check_missing_fields", {"draft": draft}),
                ("check_conflict", {"draft": draft}),
                ("create_schedule_draft", {"draft": draft}),   # Harness 在此强制中断
            ]}
        # 流程 a：口径咨询（默认）→ 只查知识库，一轮收敛
        return {"label": "口径咨询（知识库检索 → 引用作答）", "steps": [
            ("search_kb", {"query": question}),
            ("_answer", {"text": self._answer_kb}),
        ]}

    # ---- 两个 mock 最终回答组装器（把已执行步骤的结果拼成引用式回答）----

    def _answer_kb(self) -> str:
        hit = next((t for t in reversed(self.trace) if t["tool"] == "search_kb"), None)
        lines = ["候选提示（依据知识库检索）："]
        results = (hit or {}).get("result", {}).get("results", [])
        if not results:
            lines = ["制度中未找到对应口径，请人工复核。"]
        for i, h in enumerate(results, 1):
            lines.append(f"- [来源{i}] 《{h['source']}》v{h['doc_version']}/切片#{h['chunk_no']} 与查询相关，请人工核对原文。")
        lines += ["", FIXED_FOOTER]
        return "\n".join(lines)

    def _answer_verify(self) -> str:
        miss = next((t for t in reversed(self.trace) if t["tool"] == "check_missing_fields"), None)
        conf = next((t for t in reversed(self.trace) if t["tool"] == "check_conflict"), None)
        lines = ["核验候选结论："]
        if miss and not miss["result"].get("complete"):
            lines.append(f"- 资料完整性：未通过，缺项 {miss['result'].get('missing')}，需补齐后重新提交。")
        elif conf and conf["result"].get("blocking"):
            lines.append("- 冲突校验：存在硬冲突，不建议通过：")
            lines += [f"  - [{c['type']}] {c['evidence']}" for c in conf["result"].get("conflicts", []) if c.get("blocking")]
            lines.append("- 建议：调整时间/装卸口后重试，或转人工处理。")
        else:
            lines.append("- 资料完整性：通过（9 项必填字段齐备）。")
            warns = [c for c in (conf["result"].get("conflicts", []) if conf else []) if not c.get("blocking")]
            for w in warns:
                lines.append(f"- 非阻断提示：[{w['type']}] {w['evidence']}")
            lines.append("- 冲突校验：无硬冲突 → 候选结论“可通过初审”，仍需人工复核确认。")
        lines += ["", FIXED_FOOTER]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 回合入口：执行 + 问答留痕（与原 agent.py 的审计格式保持同构）
# ---------------------------------------------------------------------------

def run_harness_turn(user, question: str, draft: dict | None,
                     session_id: int | None, db: DbSession,
                     on_event=None) -> dict:
    """一次智能体回合：Harness 循环 + 决策日志 + （可能的）强制中断。"""
    cfg = get_llm_config()
    if session_id:
        session = db.query(ChatSession).get(session_id)
        if not session or session.user_id != user.id:
            raise ValueError("会话不存在")
    else:
        session = ChatSession(user_id=user.id, username=user.username,
                              title=f"[智能体] {question[:40]}")
        db.add(session)
        db.commit()
    harness = AgentHarness(user, db, session_id=session.id)
    harness._on_event = on_event
    if cfg["provider"] == "mock":
        out = harness.run_mock(question, draft)
    else:
        out = harness.run_llm(question, draft, cfg)
    tpl = db.query(PromptTemplate).filter(PromptTemplate.is_active).first()
    msg = ChatMessage(session_id=session.id, question=question,
                      retrieved_chunks=json.dumps(harness.trace, ensure_ascii=False),
                      llm_raw_answer=out["answer"],
                      prompt_template_version=tpl.version if tpl else 0, mode="agent")
    db.add(msg)
    db.commit()
    # 日志行回填 message_id（执行时 message 尚未生成，先记 None 再补）
    db.query(AgentRunLog).filter(AgentRunLog.run_id == harness.run_id,
                                 AgentRunLog.message_id.is_(None)) \
        .update({"message_id": msg.id, "session_id": session.id})
    db.commit()
    return {"session_id": session.id, "message_id": msg.id, "run_id": harness.run_id,
            "answer": out["answer"], "trace": harness.trace,
            "pending": out.get("pending"), "footer": FIXED_FOOTER}


def resume_confirmation(user, confirmation_id: int, action: str, db: DbSession) -> dict:
    """员工确认/取消后的恢复入口：校验归属与状态 → Harness.resume → 留痕新消息。"""
    rec = db.query(AgentConfirmation).get(confirmation_id)
    if not rec or rec.user_id != user.id:
        raise ValueError("确认单不存在")
    if rec.status != "pending":
        raise ValueError(f"确认单状态为 {rec.status}，不能重复处理")
    if (dt.datetime.now() - rec.created_at) > dt.timedelta(minutes=CONFIRM_EXPIRE_MINUTES):
        rec.status, rec.reject_reason, rec.decided_at = "rejected", "确认超时", now()
        db.commit()
        raise ValueError(f"确认超时（>{CONFIRM_EXPIRE_MINUTES} 分钟），请重新发起")
    harness = AgentHarness(user, db, session_id=rec.session_id)
    harness.run_id = rec.run_id + "r"          # 恢复阶段的日志关联到同一 run（加后缀）
    out = harness.resume(rec, action, user.username)
    tpl = db.query(PromptTemplate).filter(PromptTemplate.is_active).first()
    msg = ChatMessage(session_id=rec.session_id, question=f"[确认单 #{rec.id}] {'确认创建' if action == 'confirm' else '取消创建'}：{rec.question[:60]}",
                      retrieved_chunks=json.dumps(harness.trace, ensure_ascii=False),
                      llm_raw_answer=out["answer"],
                      prompt_template_version=tpl.version if tpl else 0, mode="agent")
    db.add(msg)
    db.commit()
    return {"message_id": msg.id, "answer": out["answer"], "draft_id": out["draft_id"],
            "trace": harness.trace, "footer": FIXED_FOOTER}
