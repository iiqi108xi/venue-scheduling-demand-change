# -*- coding: utf-8 -*-
"""导出与统计模块（v1.3）。

三类能力，全部只读、不产生任何生效动作：

1. 档期草稿导出
   - Markdown 交接单：给协调员线下走确认流程用，字段与系统内完全一致
   - ICS 日历：可导入 Outlook / Google Calendar / 企业微信日历做占位提醒
     （事件仍标记为 TENTATIVE 草稿状态，绝不伪装成已确认档期）

2. 冲突检测报告导出
   - 把规则引擎输出的冲突候选渲染成一页 Markdown 报告，带口径编号，
     方便粘贴进工单 / 邮件走人工复核

3. 审计统计（管理员）
   - 基于 AgentRunLog 聚合：工具调用频次、状态分布、平均耗时、
     中断确认比例等，对应 docs/10 的轨迹分析要求

设计约束（与系统级硬约束一致）：
- 导出内容仅含系统内已有数据，不做任何推断或补写
- ICS 事件 SUMMARY 一律带「【草稿】」前缀 + 状态标记，防止误当已确认档期转发
"""
from __future__ import annotations

import datetime as dt

# AgentRunLog.status 的展示顺序（统计表按此排序）
_STATUS_ORDER = ("ok", "blocked", "denied", "pending_confirm", "confirmed", "rejected", "error")


# ---------------------------------------------------------------------------
# 草稿 → Markdown 交接单
# ---------------------------------------------------------------------------

def draft_to_markdown(draft) -> str:
    """把 ScheduleDraft（或同字段 dict）渲染为 Markdown 交接单。

    独立字段映射，便于直接用 dict 做单元测试，不依赖 ORM。
    """
    def g(key: str, default: str = "") -> str:
        if isinstance(draft, dict):
            return str(draft.get(key, default) or default)
        return str(getattr(draft, key, default) or default)

    did = g("id", "-")
    lines = [
        f"# 档期交接单 · {g('project_name', '未命名项目')}",
        "",
        f"> 导出来源：场馆排期智能体系统 · 草稿 id={did} · 状态 **{g('status', 'draft')}（草稿，不生效）**",
        f"> 导出时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 一、基础信息",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 项目名称 | {g('project_name')} |",
        f"| 主办方 | {g('organizer', '—')} |",
        f"| 展馆 | {g('venue')} |",
        f"| 档期时间 | {g('event_start')} ~ {g('event_end')} |",
        f"| 装卸口 | {g('dock_no', '—')} |",
        f"| 进场开始 | {g('load_in_start', '—')} |",
        f"| 撤场结束 | {g('load_out_end', '—')} |",
        f"| 创建人 | {g('username', '—')} |",
        "",
        "## 二、备注",
        "",
        g("note") or "（无）",
        "",
        "## 三、交接确认",
        "",
        "- [ ] 排期负责人复核：档期/装卸口无冲突",
        "- [ ] 项目协调员确认：缺项已补齐",
        "- [ ] 双方确认后另行办理正式锁定手续（本单不生效）",
        "",
        "⚠️ 本交接单由系统草稿导出，仅作登记与流转，不触发锁定/签约；正式档期需人工复核确认。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 草稿 → ICS 日历（TENTATIVE 占位）
# ---------------------------------------------------------------------------

def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_dt(raw: str) -> str:
    """'2026-12-30 09:00' → '20261230T090000'（本地浮游时间，无 Z 后缀）。"""
    t = dt.datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M")
    return t.strftime("%Y%m%dT%H%M%S")


def draft_to_ics(draft) -> str:
    """生成 ICS 日历文本：活动主体一个事件 + 进撤场窗口一个事件。

    全部 VEVENT 状态为 TENTATIVE；SUMMARY 带【草稿】前缀。
    时间字段非法时抛 ValueError，由 API 层转 400。
    """
    def g(key: str, default: str = "") -> str:
        if isinstance(draft, dict):
            return str(draft.get(key, default) or default)
        return str(getattr(draft, key, default) or default)

    did = g("id", "0")
    project = g("project_name", "未命名项目")
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    ev_main_end = _ics_dt(g("event_end"))
    # 全天进撤场窗口：DTEND 需要 < 次日 00:00，这里直接用撤场结束时刻
    ev_logi_end = _ics_dt(g("load_out_end")) if g("load_out_end") else ev_main_end

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//venue-scheduling-agent//draft-export//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        # —— 活动主体 ——
        "BEGIN:VEVENT",
        f"UID:draft-{did}-main@venue-scheduling-agent",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_ics_dt(g('event_start'))}",
        f"DTEND:{ev_main_end}",
        f"SUMMARY:{_ics_escape('【草稿·不生效】' + project + '（' + g('venue') + '）')}",
        f"DESCRIPTION:{_ics_escape('系统草稿 id=' + str(did) + '，状态 ' + g('status', 'draft') + '，仅占位提醒，正式档期需人工复核确认。')}",
        f"LOCATION:{_ics_escape(g('venue'))}",
        "STATUS:TENTATIVE",
        "END:VEVENT",
        # —— 进撤场物流窗口 ——
        "BEGIN:VEVENT",
        f"UID:draft-{did}-logistics@venue-scheduling-agent",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_ics_dt(g('load_in_start'))}" if g("load_in_start") else f"DTSTART:{_ics_dt(g('event_start'))}",
        f"DTEND:{ev_logi_end}",
        f"SUMMARY:{_ics_escape('【草稿·不生效】' + project + ' 进撤场（装卸口 ' + (g('dock_no') or '待定') + '）')}",
        f"LOCATION:{_ics_escape(g('venue'))}",
        "STATUS:TENTATIVE",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# 冲突候选 → Markdown 报告
# ---------------------------------------------------------------------------

def conflict_report_markdown(draft: dict, conflicts: list[dict]) -> str:
    """把 check_conflict 的输出渲染为人工复核用的一页报告。

    conflicts 每项至少含 type / detail / basis（口径编号），
    与 tools.check_conflict 的返回结构一致；缺失字段按 '—' 兜底。
    """
    project = draft.get("project_name", "未命名项目")
    lines = [
        f"# 冲突检测报告 · {project}",
        "",
        f"> 检测时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · 检测口径：业务规范 1.2 / 2.2",
        "",
        "## 待检草稿",
        "",
        f"- 展馆：{draft.get('venue', '—')}　装卸口：{draft.get('dock_no', '—')}",
        f"- 活动时间：{draft.get('event_start', '—')} ~ {draft.get('event_end', '—')}",
        f"- 进撤场：{draft.get('load_in_start', '—')} ~ {draft.get('load_out_end', '—')}",
        "",
        f"## 冲突候选（共 {len(conflicts)} 条）",
        "",
    ]
    if not conflicts:
        lines.append("未检出硬冲突；仍需人工复核后另行办理正式锁定手续。")
    for i, c in enumerate(conflicts, 1):
        lines += [
            f"### {i}. [{c.get('type', 'unknown')}] {'（阻断）' if c.get('blocking') else '（提示）'}",
            "",
            f"- 详情：{c.get('detail', '—')}",
            f"- 口径：{c.get('basis', '—')}",
            "",
        ]
    lines += [
        "---",
        "",
        "⚠️ 以上为系统候选提示，最终结论需要人工复核确认；本报告不触发任何锁定/签约动作。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 审计统计（AgentRunLog 聚合）
# ---------------------------------------------------------------------------

def audit_stats(run_logs: list) -> dict:
    """对 AgentRunLog 行对象列表做轨迹聚合（docs/10 · 阶段5轨迹分析）。

    纯函数：输入任意具有 run_id / tool_name / status / duration_ms /
    round_no 属性的对象序列，便于用假对象做单元测试。
    """
    total = len(run_logs)
    if total == 0:
        return {"total_steps": 0, "total_runs": 0, "by_tool": {}, "by_status": {},
                "avg_duration_ms": 0, "confirm_rate": 0.0}

    by_tool: dict[str, int] = {}
    by_status: dict[str, int] = {}
    durations = 0
    runs = {r.run_id for r in run_logs}
    pending = 0
    for r in run_logs:
        by_tool[r.tool_name] = by_tool.get(r.tool_name, 0) + 1
        by_status[r.status] = by_status.get(r.status, 0) + 1
        durations += r.duration_ms or 0
        if r.status == "pending_confirm":
            pending += 1

    # 每个工具的平均耗时
    avg_by_tool: dict[str, int] = {}
    for name in by_tool:
        ds = [r.duration_ms or 0 for r in run_logs if r.tool_name == name]
        avg_by_tool[name] = round(sum(ds) / len(ds))

    by_status_ordered = {s: by_status[s] for s in _STATUS_ORDER if s in by_status}
    for s, n in sorted(by_status.items()):
        by_status_ordered.setdefault(s, n)

    return {
        "total_steps": total,
        "total_runs": len(runs),
        "avg_rounds_per_run": round(total / len(runs), 1),
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])),
        "avg_duration_by_tool": avg_by_tool,
        "by_status": by_status_ordered,
        "avg_duration_ms": round(durations / total),
        # 中断确认率：pending_confirm 步数 / 总步数（衡量人工确认门触发密度）
        "confirm_rate": round(pending / total, 3),
    }
