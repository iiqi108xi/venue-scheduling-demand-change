# -*- coding: utf-8 -*-
"""智能体工具层：6 个工具（Function Calling 规范）+ precheck_token 凭据强制。

顺序约束三层防线之第①层（代码级）：
  create_schedule_draft 必须携带 check_missing_fields(complete=true) 与
  check_conflict(blocking=false) 签发的 HMAC 凭据，服务端校验签名/有效期/草稿哈希，
  任一不满足即拒绝（428），并可作为"跳步尝试"进入审计。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import jwt
from sqlalchemy.orm import Session as DbSession

from auth import JWT_SECRET
from database import KbChunk, KbDocument, ScheduleDraft, ScheduleSlot

TOOL_TOKEN_TTL_SECONDS = 300


class ToolDenied(Exception):
    """工具执行被服务端策略拒绝（跳步/越权）。"""


class ToolError(Exception):
    """工具执行业务失败。"""


# ---------------------------------------------------------------------------
# Function Calling 规范（OpenAI tools 格式）
# ---------------------------------------------------------------------------

DRAFT_SCHEMA = {
    "type": "object",
    "description": "档期草稿字段",
    "properties": {
        "project_name": {"type": "string", "description": "项目名称"},
        "organizer": {"type": "string", "description": "主办单位"},
        "venue": {"type": "string", "description": "场地，如 主馆 Hall-A / 副馆 Hall-B / 多功能厅 Hall-C"},
        "event_start": {"type": "string", "description": "活动开始，YYYY-MM-DD HH:MM"},
        "event_end": {"type": "string", "description": "活动结束，YYYY-MM-DD HH:MM"},
        "dock_no": {"type": "string", "description": "装卸口 A/B/C/D/E"},
        "load_in_start": {"type": "string", "description": "进场开始"},
        "load_out_end": {"type": "string", "description": "撤场结束"},
    },
    "required": ["project_name", "organizer", "venue", "event_start", "event_end"],
}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "search_kb",
        "description": "在场馆排期制度/场地规则/变更规范/冲突口径知识库中做混合检索。凡涉及口径、规则、判定标准的回答，必须先调用本工具，禁止凭记忆作答。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "top_k": {"type": "integer", "default": 3}},
            "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "get_schedule",
        "description": "查询场地在指定日期范围/编号下的现行档期快照（仅 confirmed 为冲突判定基准）。",
        "parameters": {"type": "object", "properties": {
            "venue": {"type": "string"}, "date_range": {"type": "string"},
            "request_no": {"type": "string"}}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "check_missing_fields",
        "description": "校验档期草稿 9 项必填字段，返回缺项清单；通过时签发 precheck_token 的一半。",
        "parameters": {"type": "object", "properties": {"draft": DRAFT_SCHEMA}, "required": ["draft"]},
    }},
    {"type": "function", "function": {
        "name": "check_conflict",
        "description": "对档期草稿做多维冲突检测（时间窗重叠/装卸口占用/进撤场时长），返回冲突候选与依据；无硬冲突时签发 precheck_token 的另一半。",
        "parameters": {"type": "object", "properties": {"draft": DRAFT_SCHEMA}, "required": ["draft"]},
    }},
    {"type": "function", "function": {
        "name": "create_schedule_draft",
        "description": "创建档期草稿（不生效）。必须同时携带 check_missing_fields 签发的 precheck_token_missing 与 check_conflict 签发的 precheck_token_conflict；试点期仅协调员/管理员可用。",
        "parameters": {"type": "object", "properties": {
            "draft": DRAFT_SCHEMA,
            "precheck_token_missing": {"type": "string", "description": "check_missing_fields 通过后签发"},
            "precheck_token_conflict": {"type": "string", "description": "check_conflict 通过后签发"}},
            "required": ["draft", "precheck_token_missing", "precheck_token_conflict"]},
    }},
    {"type": "function", "function": {
        "name": "submit_for_review",
        "description": "将草稿置为 pending_review 等待人工复核。这是智能体能触达的最后一个状态，confirmed 只能由人工触发。",
        "parameters": {"type": "object", "properties": {
            "draft_id": {"type": "integer"}}, "required": ["draft_id"]},
    }},
]

# ---------------------------------------------------------------------------
# precheck_token（HMAC 签名，绑定草稿哈希 + 类型 + 有效期）
# ---------------------------------------------------------------------------

def _norm_draft(draft: dict) -> dict:
    return {k: str(draft.get(k, "")).strip() for k in (
        "project_name", "organizer", "venue", "event_start", "event_end",
        "dock_no", "load_in_start", "load_out_end")}


def draft_hash(draft: dict) -> str:
    return hashlib.sha256(json.dumps(_norm_draft(draft), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def _sign(payload: dict) -> str:
    payload = {**payload, "exp": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=TOOL_TOKEN_TTL_SECONDS)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _verify(token: str, expect_type: str, dhash: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise ToolDenied("precheck_token 已过期（5 分钟有效），请重新校验") from e
    except jwt.InvalidTokenError as e:
        raise ToolDenied("precheck_token 无效") from e
    if payload.get("type") != expect_type:
        raise ToolDenied(f"precheck_token 类型不符，期望 {expect_type}")
    if payload.get("draft_hash") != dhash:
        raise ToolDenied("precheck_token 与本次草稿内容不匹配（疑似校验 A、提交 B），已拒绝")
    if not payload.get("ok"):
        raise ToolDenied("precheck_token 签发时校验未通过")
    return payload


# ---------------------------------------------------------------------------
# 时间解析与冲突规则引擎（blocking：时间窗重叠、装卸口占用；warning：进撤场时长）
# ---------------------------------------------------------------------------

def parse_dt(s: str) -> dt.datetime | None:
    s = str(s).strip().replace("T", " ")
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
        except ValueError:
            return None


def _overlap(a1, a2, b1, b2) -> bool:
    return a1 < b2 and b1 < a2


def check_missing_fields_impl(draft: dict) -> dict:
    d = _norm_draft(draft)
    required = [("project_name", "项目名称"), ("organizer", "主办单位"), ("venue", "场地"),
                ("event_start", "活动开始时间"), ("event_end", "活动结束时间"),
                ("dock_no", "装卸口"), ("load_in_start", "进场开始时间"), ("load_out_end", "撤场结束时间")]
    missing = [label for key, label in required if not d.get(key)]
    for key in ("event_start", "event_end", "load_in_start", "load_out_end"):
        if d.get(key) and parse_dt(d[key]) is None:
            missing.append(f"{key} 时间格式无法解析")
    complete = not missing
    result = {"missing": missing, "complete": complete, "draft_hash": draft_hash(d)}
    if complete:
        result["precheck_token"] = _sign({"type": "missing", "draft_hash": result["draft_hash"], "ok": True})
    return result


def check_conflict_impl(draft: dict, db: DbSession) -> dict:
    d = _norm_draft(draft)
    conflicts: list[dict] = []
    es, ee = parse_dt(d.get("event_start")), parse_dt(d.get("event_end"))
    li, lo = parse_dt(d.get("load_in_start")), parse_dt(d.get("load_out_end"))
    if not es or not ee:
        return {"conflicts": [], "blocking": True, "error": "活动起止时间缺失或格式错误，无法检测",
                "draft_hash": draft_hash(d)}

    slots = db.query(ScheduleSlot).filter(ScheduleSlot.status == "confirmed").all()
    for s in slots:
        ss, se = parse_dt(s.event_start), parse_dt(s.event_end)
        if ss and se and s.venue == d.get("venue") and _overlap(es, ee, ss, se):
            conflicts.append({"type": "time_overlap", "blocking": True,
                              "evidence": f"与已确认档期 {s.request_no}《{s.project_name}》时间窗重叠"
                                          f"（{s.event_start} ~ {s.event_end}），口径：业务规范 1.2",
                              "counterpart": s.request_no})
        if (d.get("dock_no") and s.dock_no == d.get("dock_no") and li and lo):
            sli, slo = parse_dt(s.load_in_start), parse_dt(s.load_out_end)
            if sli and slo and _overlap(li, lo, sli, slo):
                conflicts.append({"type": "dock_conflict", "blocking": True,
                                  "evidence": f"装卸口 {d['dock_no']} 在进撤场窗口内被 {s.request_no} 占用"
                                              f"（{s.load_in_start} ~ {s.load_out_end}），口径：业务规范 2.2",
                                  "counterpart": s.request_no})

    # 进撤场时长标准（业务规范 3.1）：warning，不 blocking（豁免属二期，本期一并终止提示人工处理）
    if li and lo:
        load_h = (lo - li).total_seconds() / 3600
        if load_h < 12:
            conflicts.append({"type": "load_duration_violation", "blocking": False,
                              "evidence": f"总占用时长 {load_h:.1f}h，低于搭建类进场≥24h/撤场≥12h 标准，口径：业务规范 3.1，需人工确认活动类型"})

    blocking = any(c["blocking"] for c in conflicts)
    result = {"conflicts": conflicts, "blocking": blocking, "draft_hash": draft_hash(d)}
    if not blocking:
        result["precheck_token"] = _sign({"type": "conflict", "draft_hash": result["draft_hash"], "ok": True})
    return result


# ---------------------------------------------------------------------------
# 工具实现（编排器通过 TOOL_IMPL 调用；写工具在此层做角色门禁）
# ---------------------------------------------------------------------------

S4_ALLOWED_ROLES = ("coordinator", "admin")


def t_search_kb(query: str, db: DbSession, top_k: int = 3) -> dict:
    from rag import hybrid_search, get_llm_config

    chunks = [{"id": c.id, "source": d.filename, "doc_id": d.id, "doc_version": d.version,
               "chunk_no": c.chunk_no, "content": c.content}
              for c, d in db.query(KbChunk, KbDocument).join(KbDocument).filter(KbDocument.status == "ready").all()]
    results = hybrid_search(query, chunks, _get_index(), get_llm_config())
    return {"results": [{"source": r["source"], "doc_version": r["doc_version"],
                         "chunk_no": r["chunk_no"], "text": r["content"]} for r in results]}


def _get_index():
    from api import faiss_index
    return faiss_index


def t_get_schedule(db: DbSession, venue: str = "", date_range: str = "", request_no: str = "") -> dict:
    q = db.query(ScheduleSlot).filter(ScheduleSlot.status == "confirmed")
    if venue:
        q = q.filter(ScheduleSlot.venue == venue)
    if request_no:
        q = q.filter(ScheduleSlot.request_no == request_no)
    slots = q.all()
    out = []
    for s in slots:
        if date_range and "~" in date_range:
            ds, de = date_range.split("~", 1)
            ss, se = parse_dt(s.event_start), parse_dt(s.event_end)
            b1, b2 = parse_dt(ds.strip()), parse_dt(de.strip()) if parse_dt(de.strip()) else dt.datetime.max
            if ss and se and not (ss <= b2 and se >= b1):
                continue
        out.append({"request_no": s.request_no, "project_name": s.project_name, "venue": s.venue,
                    "event_start": s.event_start, "event_end": s.event_end, "dock_no": s.dock_no,
                    "load_in_start": s.load_in_start, "load_out_end": s.load_out_end})
    return {"schedules": out, "note": "仅返回 confirmed 状态快照，in_review 版本仅作参考"}


def t_create_schedule_draft_impl(draft: dict, token_missing: str, token_conflict: str,
                                 user_id: int, user_role: str, user_name: str, db: DbSession):
    """写工具：凭据强制 + 角色门禁（S4 试点期仅协调员/管理员）。"""
    if user_role not in S4_ALLOWED_ROLES:
        raise ToolDenied(f"S4 草稿创建试点期仅 {'/'.join(S4_ALLOWED_ROLES)} 可用，当前角色 {user_role}")
    d = _norm_draft(draft)
    dh = draft_hash(d)
    _verify(token_missing, "missing", dh)     # 缺项校验凭据（绑定同一草稿哈希）
    _verify(token_conflict, "conflict", dh)   # 冲突校验凭据（blocking=true 时根本不会签发）
    draft_row = ScheduleDraft(
        user_id=user_id, username=user_name,
        project_name=d["project_name"], venue=d["venue"],
        event_start=d["event_start"], event_end=d["event_end"],
        dock_no=d["dock_no"], load_in_start=d["load_in_start"], load_out_end=d["load_out_end"],
        note=json.dumps({"organizer": d["organizer"], "via": "agent", "precheck": "passed"}, ensure_ascii=False),
        status="draft",
    )
    db.add(draft_row)
    db.commit()
    return {"ok": True, "draft_id": draft_row.id, "status": draft_row.status,
            "notice": "草稿已创建（不生效），已自动进入待复核流程"}


def t_submit_for_review_impl(draft_id: int, user_role: str, user_name: str, db: DbSession) -> dict:
    if user_role not in S4_ALLOWED_ROLES:
        raise ToolDenied(f"提交复核试点期仅 {'/'.join(S4_ALLOWED_ROLES)} 可用")
    row = db.query(ScheduleDraft).get(draft_id)
    if not row:
        raise ToolError("草稿不存在")
    if row.status != "draft":
        raise ToolError(f"草稿状态为 {row.status}，不能重复提交")
    row.status = "pending_review"
    db.commit()
    return {"ok": True, "draft_id": draft_id, "status": "pending_review",
            "notice": "已提交人工复核，最终结论需人工确认"}
