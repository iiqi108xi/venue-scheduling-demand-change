# -*- coding: utf-8 -*-
"""采集问答助手真实会话并导出归档到 src/1-问答助手归档/。

运行前提：本地服务 http://127.0.0.1:8511 已启动（DB_PATH 指向 data/venue_scheduling.db）。
产物：
  - 对话归档.md            人类可读的会话实录（提问/召回片段/模型原文/角色）
  - chat_messages.json     chat_messages 表原始留痕
  - agent_run_logs.json    agent 工具执行轨迹留痕
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import requests

BASE = "http://127.0.0.1:8511"
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "1-问答助手归档")
DB = os.path.join(os.path.dirname(__file__), "..", "data", "venue_scheduling.db")


def mk():
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "qa-archive-collector/1.0"})
    return s


ADMIN, STAFF = mk(), mk()


def login(sess, u, p):
    r = sess.post(BASE + "/api/auth/login", json={"username": u, "password": p}, timeout=30)
    r.raise_for_status()
    tok = r.json()["token"]
    sess.headers.update({"Authorization": "Bearer " + tok})


login(ADMIN, "admin", "admin123")
login(STAFF, "staff", "staff123")

QA_ADMIN = [
    ("装卸口（dock）占用冲突的判定口径是什么？", "知识库·业务规范"),
    ("申请新增一个档期，主办方需要提前多少天提交？超期怎么处理？", "知识库·业务规范"),
    ("哪些动作属于不可逆动作，系统里如何拦截？", "知识库·业务规范"),
    ("档期变更需要哪些角色审批？", "知识库·业务规范"),
]
QA_STAFF = [
    ("展位搭建期间可以把货物临时堆放在主馆通道吗？", "知识库·业务规范"),
]

t0 = time.time()
for q, tag in QA_ADMIN:
    r = ADMIN.post(BASE + "/api/chat", json={"question": q}, timeout=60)
    assert r.status_code == 200, (q, r.status_code, r.text[:200])
for q, tag in QA_STAFF:
    r = STAFF.post(BASE + "/api/chat", json={"question": q}, timeout=60)
    assert r.status_code == 200, (q, r.status_code, r.text[:200])

# 一条 agent 会话（含强制中断确认单 → 人工确认 → 恢复）
DRAFT = {"project_name": "秋季医疗器械展", "organizer": "东浩会展", "venue": "主馆 Hall-A",
         "event_start": "2027-09-10 09:00", "event_end": "2027-09-12 17:00",
         "dock_no": "D03", "load_in_start": "2027-09-08 08:00", "load_out_end": "2027-09-13 12:00"}
r = ADMIN.post(BASE + "/api/agent/run", json={"question": "申请新增档期并核对冲突", "draft": DRAFT}, timeout=60)
assert r.status_code == 200, r.text[:300]
pend = (r.json() or {}).get("pending") or {}
cid = pend.get("confirmation_id") or pend.get("id")
r2 = ADMIN.post(BASE + "/api/agent/confirm/%s" % cid, json={"action": "confirm"}, timeout=60)
print("agent run + confirm:", r.status_code, r2.status_code, cid)

# staff 角色门禁示例（P2 权限证据）
r3 = STAFF.get(BASE + "/api/admin/users", timeout=30)
print("staff GET /api/admin/users ->", r3.status_code)

# ---------------- 导出 ----------------
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
msgs = [dict(r) for r in con.execute(
    "select m.*, s.username, u.role from chat_messages m "
    "join chat_sessions s on s.id=m.session_id left join users u on u.id=s.user_id order by m.id")]
runlogs = [dict(r) for r in con.execute("select * from agent_run_logs order by id")]
con.close()

for row in msgs:
    try:
        row["retrieved_chunks"] = json.loads(row.get("retrieved_chunks") or "[]")
    except Exception:
        pass

json.dump({"exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": BASE,
           "chat_messages": msgs}, open(os.path.join(OUT, "chat_messages.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
json.dump({"exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "agent_run_logs": runlogs},
          open(os.path.join(OUT, "agent_run_logs.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("exported messages:", len(msgs), "runlogs:", len(runlogs))
