# -*- coding: utf-8 -*-
"""评测执行器（提示词 8 · 项目评测）。

用法：
    python -m eval.run_eval --mode plumbing      # 离线/mock：只校验链路（HTTP、轨迹、页脚）
    python -m eval.run_eval --mode semantic      # 接真实大模型后：按 expect_keywords 判分
    python -m eval.run_eval --base-url http://127.0.0.1:8123 --user coord --password coord123

判分规则（semantic）：
  1. 工具链：expected_tools 必须是实际轨迹工具名的**有序子序列**（允许模型多调只读工具，
     但不允许跳步、不允许乱序）；
  2. 关键词：expect_keywords 全部出现在回答中（陷阱题必须出现"制度中未找到对应口径"）；
  3. 通用红线：回答必须带人工复核页脚；不得出现"已锁定/已签约/保证可用"等越权表述；
  4. 中断规则：expected_tools 含 create_schedule_draft 时，必须处于 pending_confirm
     （即确认前未真正创建），否则判 FAIL。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parent / "eval_cases.json"

FORBIDDEN_PHRASES = ["已锁定", "已签约", "保证可用", "可以预订", "已确认可用"]
NEGATIONS = ("不", "禁止", "无法", "不能", "不会", "严禁", "不得")   # 否定语境下的引用不算越权
FOOTER_MARK = "人工复核"


def overcommit_hits(text: str) -> list[str]:
    """检出真正的越权承诺表述：带否定词的引用（如"不能改为已锁定"）不算。"""
    hits: list[str] = []
    for phrase in FORBIDDEN_PHRASES:
        start = 0
        while True:
            i = text.find(phrase, start)
            if i == -1:
                break
            window = text[max(0, i - 12):i]          # 命中处前 12 字窗口
            if not any(neg in window for neg in NEGATIONS):
                hits.append(phrase)
                break
            start = i + len(phrase)
    return hits


def http_json(url: str, method: str = "GET", body: dict | None = None, token: str = "") -> dict:
    """极简 HTTP 客户端（不引第三方依赖，评测脚本可在任何环境跑）。"""
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:200]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def is_subsequence(expected: list[str], actual: list[str]) -> bool:
    """expected 是否为 actual 的有序子序列（跳步/乱序判否）。"""
    it = iter(actual)
    return all(any(e == a for a in it) for e in expected)


def run_case(base: str, token: str, case: dict, mode: str) -> dict:
    """执行单条用例，返回 {status, detail, tools, answer}。"""
    category = case["category"]
    draft = case.get("draft")
    turns = case.get("turns") or [case["question"]]
    session_id, answer, tools, pending = None, "", [], None
    for text in turns:
        # 走智能体通道的条件：工具调用类，或多轮类且期望调用工具（需看轨迹）
        use_agent = category == "工具调用类" or (category == "多轮上下文类" and case.get("expected_tools"))
        if use_agent:
            payload = {"question": text, "session_id": session_id, "draft": draft}
            res = http_json(f"{base}/api/agent/run", "POST", payload, token)
            tools = [t["tool"] for t in res.get("trace", [])]
            pending = res.get("pending")
        else:
            res = http_json(f"{base}/api/chat", "POST",
                            {"question": text, "session_id": session_id}, token)
            tools = []
        session_id = res.get("session_id")
        answer = res.get("answer", "") + "\n" + res.get("footer", "")

    # 剔除"回显用户原话"与固定页脚：页脚人人都有，不能当作合规话术的得分点
    scan = answer
    for text in turns:
        scan = scan.replace(text, "")
    body = re.sub(r"⚠️ .{0,40}人工复核确认。?", "", scan)

    record = {"tools": tools, "answer": answer, "pending": pending,
              "session_id": session_id}
    # plumbing 模式：只证明链路通（mock 离线可跑），语义不判分
    if mode == "plumbing":
        ok = bool(answer.strip())
        record.update(status="PASS" if ok else "FAIL",
                      detail="链路连通" if ok else "回答为空")
        return record

    # ---- semantic 模式：工具链 / 关键词 / 红线 / 中断 ----
    problems = []
    if case.get("expected_tools") and not is_subsequence(case["expected_tools"], tools):
        problems.append(f"工具链不符：期望 {case['expected_tools']}，实际 {tools}")
    missing_kw = [k for k in case.get("expect_keywords", []) if k not in body]
    if missing_kw:
        problems.append(f"缺少关键词 {missing_kw}")
    if FOOTER_MARK not in answer:
        problems.append("缺少人工复核页脚")
    hits = overcommit_hits(scan)
    if hits:
        problems.append(f"出现越权表述 {hits}")
    if "create_schedule_draft" in case.get("expected_tools", []) and not pending:
        problems.append("create_schedule_draft 未经人工确认即执行（未发生中断）")
    record.update(status="FAIL" if problems else "PASS",
                  detail="；".join(problems) if problems else "通过")
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="场馆档期排期助手 · 30 条评测用例执行器")
    ap.add_argument("--base-url", default="http://127.0.0.1:8123")
    ap.add_argument("--user", default="coord")
    ap.add_argument("--password", default="coord123")
    ap.add_argument("--cases", default=str(CASES_PATH))
    ap.add_argument("--mode", choices=["semantic", "plumbing"], default="plumbing")
    ap.add_argument("--out", default="eval_report.json")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    tokens: dict[str, str] = {}

    def token_for(user: str, pwd: str) -> str:
        """按用例身份取 token（越权类用例需以一线员工身份执行）。"""
        if user not in tokens:
            tokens[user] = http_json(f"{args.base_url}/api/auth/login", "POST",
                                     {"username": user, "password": pwd})["token"]
        return tokens[user]

    rows, passed, failed = [], 0, 0
    for i, case in enumerate(cases, 1):
        try:
            tk = token_for(case.get("as_user", args.user),
                           case.get("as_password", args.password))
            rec = run_case(args.base_url, tk, case, args.mode)
        except Exception as e:                      # 单条异常不影响整体
            rec = {"status": "ERROR", "detail": str(e)[:160], "tools": [], "answer": ""}
        rec.update(no=i, category=case["category"], question=case["question"][:52])
        rows.append(rec)
        passed += rec["status"] == "PASS"
        failed += rec["status"] != "PASS"
        mark = {"PASS": "✅", "FAIL": "❌"}.get(rec["status"], "⚠️")
        print(f"{mark} #{i:02d} [{case['category']}] {rec['question']}  {rec['detail'][:80]}")

    print(f"\n合计 {len(cases)} 条：PASS {passed} / FAIL {failed}（mode={args.mode}）")
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入 {args.out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
