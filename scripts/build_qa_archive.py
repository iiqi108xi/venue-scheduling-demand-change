# -*- coding: utf-8 -*-
"""为 src/1-问答助手归档 生成 UI 截图 + 可读对话实录 md。"""
from __future__ import annotations

import json
import os
import sqlite3

from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "src", "1-问答助手归档")
BASE = "http://127.0.0.1:8511"

# ---------------- UI 截图 ----------------
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=r"C:/Program Files/Google/Chrome/Application/chrome.exe")
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    pg = ctx.new_page()
    pg.goto(BASE + "/login", wait_until="networkidle")
    pg.fill("#username", "admin")
    pg.fill("#password", "admin123")
    pg.click("button.btn-primary")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(800)

    pg.goto(BASE + "/chat", wait_until="networkidle")
    pg.fill("#question" if pg.locator("#question").count() else "textarea", "装卸口（dock）占用冲突的判定口径是什么？")
    pg.click("button.btn-primary")
    try:
        pg.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    pg.wait_for_timeout(3000)
    pg.screenshot(path=os.path.join(OUT, "截图-问答页.png"), full_page=True)

    pg.goto(BASE + "/chat/history", wait_until="networkidle")
    pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(OUT, "截图-历史会话.png"), full_page=True)

    pg.goto(BASE + "/admin/audit", wait_until="networkidle")
    pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(OUT, "截图-审计页.png"), full_page=True)
    b.close()
print("screenshots done")

# ---------------- 对话实录 md ----------------
con = sqlite3.connect(os.path.join(ROOT, "data", "venue_scheduling.db"))
con.row_factory = sqlite3.Row
msgs = [dict(r) for r in con.execute(
    "select m.*, s.username, u.role from chat_messages m join chat_sessions s on s.id=m.session_id "
    "left join users u on u.id=s.user_id order by m.id")]
runs = [dict(r) for r in con.execute("select * from agent_run_logs order by id")]
con.close()

md = []
md.append("# 问答助手 · 会话归档\n")
md.append("> 归档时间：2026-11-06 · 数据来源：`data/venue_scheduling.db`（`chat_sessions` / `chat_messages` / `agent_run_logs` 表）\n")
md.append("> 留痕字段：提问原文、召回片段（含文档名/版本/切片号）、大模型原始回答全文、提问人/角色、提示词模板版本、时间戳。\n")
md.append("> 本文件为人类可读实录；机器可读原始留痕见同目录 `chat_messages.json`、`agent_run_logs.json`；界面截图见 3 张 PNG。\n")

cur_sid, n = None, 0
for m in msgs:
    n += 1
    if m["session_id"] != cur_sid:
        cur_sid = m["session_id"]
        md.append("\n---\n")
        md.append("## 会话 #%d · %s（%s） · %s\n" % (
            cur_sid, m.get("username"), m.get("role") or "-", (m["created_at"] or "")[:19]))
    md.append("\n**问（第%d轮）**：%s\n" % (n, m["question"]))
    chunks = m.get("retrieved_chunks") or "[]"
    try:
        cl = json.loads(chunks) if isinstance(chunks, str) else chunks
    except Exception:
        cl = []
    if cl:
        md.append("\n召回片段：%s\n" % "；".join(
            "%s/切片#%s" % (c.get("source"), c.get("chunk_no")) for c in cl))
    ans = (m.get("llm_raw_answer") or "").strip()
    md.append("\n**答（模型原文）**：\n\n%s\n" % (ans[:1500] + ("…（截断，全文见 JSON）" if len(ans) > 1500 else "")))
    md.append("\n*留痕：mode=%s · 模板v%s · %s*\n" % (m.get("mode"), m.get("prompt_template_version"), (m["created_at"] or "")[:19]))

md.append("\n---\n\n## Agent 工具执行轨迹（节选 · 最近 12 条）\n")
md.append("\n| 轮次 | 工具 | 参数摘要 | 状态 | 耗时ms | 时间 |\n|---|---|---|---|---|---|\n")
for r in runs[-12:]:
    param = (str(r.get("params") or r.get("arguments") or ""))[:60].replace("|", "\\|").replace("\n", " ")
    md.append("| %s | %s | `%s` | %s | %s | %s |\n" % (
        r.get("turn", r.get("round", "-")), r.get("tool", r.get("tool_name", "-")), param,
        r.get("status", "-"), r.get("elapsed_ms", r.get("duration_ms", "-")), (str(r.get("created_at") or ""))[:19]))

open(os.path.join(OUT, "对话归档.md"), "w", encoding="utf-8").write("".join(md))
print("对话归档.md:", os.path.getsize(os.path.join(OUT, "对话归档.md")) // 1024, "KB, 消息", len(msgs))
