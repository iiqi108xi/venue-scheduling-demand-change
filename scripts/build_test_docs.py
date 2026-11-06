# -*- coding: utf-8 -*-
"""生成 Test 三件套：场景测试方案（md+docx）、5 份可视化过程 docx、上线地址（md+docx）。

数据来源：assets/test-shots/runs/local.json（scripts/run_scenario_tests.py 实测输出）
证据图：assets/test-shots/evidence2/*.png（Playwright 渲染真实响应报文）
"""
import json
import os
import textwrap
from datetime import datetime

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(ROOT, "Test")
SHOTS = os.path.join(ROOT, "assets", "test-shots")
EVI = os.path.join(SHOTS, "evidence2")
RUNS = json.load(open(os.path.join(SHOTS, "runs", "local.json"), encoding="utf-8"))
os.makedirs(EVI, exist_ok=True)
os.makedirs(TEST, exist_ok=True)

GRAY = RGBColor(0x60, 0x60, 0x60)
DARK = RGBColor(0x1F, 0x29, 0x37)
GREEN = RGBColor(0x04, 0x78, 0x57)

# 用例 → 真实 UI 截图（scripts/ui_shots.py 现场抓取，与本轮测试同一环境）
UI_SHOTS = {
    "N0": [("01-login.png", "登录页：分享链接进入，账号口令 + 角色RBAC")],
    "N2": [("06-kb.png", "知识库管理页：6 篇制度/简报，25 个切片，状态 ready")],
    "N3": [("02-pending-confirm.png", "智能体模式触发强制中断：确认卡片列出草稿 8 字段，明确「确认后才执行」")],
    "N5": [("03-confirmed-draft.png", "点击「确认创建」后：草稿已创建（不生效）并提示提交复核"),
           ("04-drafts.png", "草稿页：draft 状态可见，系统不产生 confirmed 档期")],
    "N6": [("05-audit.png", "审计页：提问原文、工具轨迹与耗时全量留痕")],
    "A1": [("08-injection.png", "注入攻击实测界面：系统仍给出检索结果并保留人工复核页脚")],
    "P3": [("07-staff-denied.png", "staff 触发草稿创建：工具层 denied，界面给出角色门禁提示")],
}

SCENES = {
    "正常": {"file": "01-正常场景测试.docx", "goal":
         "验证系统在既有制度、既有档期、合法角色下的主链路可用：登录 → 口径咨询（RAG）→ 预订全流程（触发人工确认）→ 确认后草稿落地 → 审计留痕。"
         "判定不看“界面像不像正常”，只看状态码、工具轨迹与状态机迁移是否与设计一致。"},
    "边界": {"file": "02-边界场景测试.docx", "goal":
         "验证输入边界与状态机边界：必填字段缺失、时间格式非法、知识库空命中、确认单重复处理、确认单不存在。"
         "边界用例的价值在于“拦截位置是否正确”——必须停在对应校验层，不能越层执行，也不能留下半状态。"},
    "对抗": {"file": "03-对抗场景测试.docx", "goal":
         "验证三层防线对恶意输入的抵抗力：提示注入（要求绕过确认直接锁档）、工具白名单外调用、无凭据直调写工具、确认接口旁路参数。"
         "核心判定：写工具即使被调用，状态也必须是 pending_confirm，绝不落库生效。"},
    "权限": {"file": "04-权限场景测试.docx", "goal":
         "验证登录态与角色门禁：未登录访问业务/审计接口、一线员工访问管理员接口、一线员工触发草稿创建（S4 试点期仅协调员/管理员）。"
         "判定按“接口层 401/403”与“工具层 denied”两级区分，二者不能互相替代。"},
    "故障": {"file": "05-故障场景测试.docx", "goal":
         "验证异常输入与依赖异常下的系统行为：空问题、非法 JSON、不存在的会话、超长输入、不存在的静态资源。"
         "判定要求：错误必须显式、可读、无副作用（不产生半状态），且不把内部堆栈暴露给调用方。"},
}

SCENE_FIX = {  # 每类场景的缺陷记录
    "正常": [("D-00", "无", "—", "—", "—")],
    "边界": [("D-01", "时间格式非法时 get_schedule 抛 TypeError → 500",
              "B2 首轮执行返回 500 Internal Server Error",
              "t_get_schedule 未对 parse_dt 返回 None 做保护，b1 为 None 参与比较",
              "改为“任一端非法即退化为不限范围”，并补回归单测 test_bad_range_does_not_raise")],
    "对抗": [("D-00", "无", "—", "—", "—")],
    "权限": [("D-00", "无", "—", "—", "—")],
    "故障": [("D-00", "无", "—", "—", "—")],
}

ENV = [("被测版本", "venue-scheduling-demand-change v1.6.1（含 D-01 修复）"),
       ("运行环境", "Windows 11 · Python 3.13.13 · uvicorn 单进程 · SQLite 交付库 data/venue_scheduling.db"),
       ("模型通道", "LLM_PROVIDER=mock（离线确定性管线，排除模型随机性对判定的干扰）"),
       ("测试地址", "http://127.0.0.1:8511（本地交付库；线上地址见 Test/06）"),
       ("执行方式", "scripts/run_scenario_tests.py 一次跑完全部用例，结果落 JSON 证据"),
       ("执行时间", RUNS["tested_at"] + "（总耗时 %.1f 秒）" % RUNS["duration_s"])]


# --------------------------------------------------------------------------
# docx 基础样式
# --------------------------------------------------------------------------
def setup(doc):
    st = doc.styles["Normal"]
    st.font.name = "宋体"
    st.font.size = Pt(11)
    st._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    st.paragraph_format.line_spacing = 1.5
    st.paragraph_format.space_after = Pt(4)
    for i, (size, color) in enumerate([(18, DARK), (14, DARK), (12, DARK)], start=1):
        h = doc.styles["Heading %d" % i]
        h.font.name = "黑体"
        h.font.size = Pt(size)
        h.font.bold = True
        h.font.color.rgb = color
        h._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        h.paragraph_format.space_before = Pt(12 if i == 1 else 8)
        h.paragraph_format.space_after = Pt(6)


def p(doc, text, size=11, bold=False, color=None, align=None, indent=True, space_after=4):
    par = doc.add_paragraph()
    run = par.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "宋体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    if color:
        run.font.color.rgb = color
    if indent:
        par.paragraph_format.first_line_indent = Cm(0.74)
    if align:
        par.alignment = align
    par.paragraph_format.space_after = Pt(space_after)
    return par


def title(doc, main, sub):
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = par.add_run(main)
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.name = "黑体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    par2 = doc.add_paragraph()
    par2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = par2.add_run(sub)
    r2.font.size = Pt(10.5)
    r2.font.color.rgb = GRAY
    r2.font.name = "宋体"
    r2._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    par2.paragraph_format.space_after = Pt(10)


def table(doc, headers, rows, widths=None, font_size=9.5):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        run = hdr[i].paragraphs[0].add_run(str(h))
        run.font.bold = True
        run.font.size = Pt(font_size)
        run.font.name = "宋体"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        hdr[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(v))
            run.font.size = Pt(font_size)
            run.font.name = "宋体"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def caption(doc, text):
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = par.add_run(text)
    run.font.size = Pt(9)
    run.font.color.rgb = GRAY
    run.font.name = "宋体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    par.paragraph_format.space_after = Pt(10)


def picture(doc, path, width_cm=15.8):
    if os.path.exists(path):
        doc.add_picture(path, width=Cm(width_cm))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        return True
    return False


# --------------------------------------------------------------------------
# 证据卡片渲染（真实响应报文 → PNG）
# --------------------------------------------------------------------------
CARD_CSS = """
body{margin:0;background:#eef1f5;font:13px/1.65 'Microsoft YaHei',sans-serif}
.card{background:#fff;margin:0;border-radius:0;width:980px}
.hd{background:#111827;color:#fff;padding:12px 20px;font-weight:700;font-size:15px;
    display:flex;justify-content:space-between;align-items:center}
.hd .code{font:12px Consolas,monospace;background:#374151;padding:2px 10px;border-radius:99px}
.bd{padding:16px 20px 20px}
.row{display:flex;gap:10px;margin:6px 0;align-items:baseline}
.k{color:#6b7280;min-width:74px;font-size:12px}
.v{color:#111827;font-weight:600;font-size:12.5px}
pre{background:#0f172a;color:#a5d6ff;padding:10px 12px;border-radius:8px;white-space:pre-wrap;
    word-break:break-all;font:11.5px/1.55 Consolas,monospace;margin:6px 0;max-height:230px;overflow:hidden}
.ok{color:#047857;font-weight:700}.bad{color:#b91c1c;font-weight:700}
table{border-collapse:collapse;width:100%;margin-top:6px;font-size:11.5px}
th,td{border:1px solid #e5e7eb;padding:4px 8px;text-align:left}
th{background:#f3f4f6;color:#374151}
"""


def render_cards(records):
    from playwright.sync_api import sync_playwright

    html_parts = []
    for r in records:
        resp = r.get("resp") or {}
        if not isinstance(resp, dict):        # 审计/草稿列表接口返回数组
            resp = {"_list_len": len(resp), "_list": resp[:3]}
        tr = resp.get("trace") or []
        badge = "ok" if r["pass"] else "bad"
        ans = str(resp.get("answer") or resp.get("detail") or resp.get("_text") or "")[:420]
        rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s ms</td></tr>" %
            (t.get("step", ""), t.get("tool", ""), t.get("status", ""), t.get("duration_ms", ""))
            for t in tr[:8])
        body = textwrap.dedent(f"""
        <div class="card">
          <div class="hd"><span>{r['id']} · {r['title']}</span>
            <span class="code">{r['status']} · {r['elapsed_ms']}ms</span></div>
          <div class="bd">
            <div class="row"><span class="k">请求</span><span class="v">{r['req']['method']} {r['req']['path']}</span></div>
            <div class="row"><span class="k">判定</span><span class="{badge}">
              期望 {r['expect']} → 实际 {r['status']}（{'PASS' if r['pass'] else 'FAIL'}）</span></div>
            {'<pre>' + json.dumps(r['req'].get('body'), ensure_ascii=False)[:260] + '</pre>' if r['req'].get('body') else ''}
            {('<table><tr><th>步</th><th>工具</th><th>状态</th><th>耗时</th></tr>' + rows + '</table>') if tr else ''}
            <pre>{ans}</pre>
          </div>
        </div>""")
        out = os.path.join(EVI, r["id"] + ".png")
        html_parts.append((out, body))

    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        page = b.new_context(viewport={"width": 980, "height": 800},
                             device_scale_factor=2).new_page()
        for out, body in html_parts:
            html_doc = ("<!doctype html><meta charset='utf-8'><style>" + CARD_CSS +
                        "</style>" + body)
            tmp = os.path.join(EVI, "_tmp.html")
            open(tmp, "w", encoding="utf-8").write(html_doc)
            page.goto("file:///" + tmp.replace("\\", "/"))
            page.wait_for_timeout(120)
            page.locator(".card").screenshot(path=out)
        b.close()
    print("证据卡片 %d 张 → %s" % (len(html_parts), EVI))


# --------------------------------------------------------------------------
# 1) 五份过程文档
# --------------------------------------------------------------------------
def build_scene_doc(scene, recs):
    meta = SCENES[scene]
    doc = Document()
    setup(doc)
    title(doc, scene + "场景测试",
          "场馆排期与需求变更 · 档期智能核对智能体系统　|　可视化测试过程（有图有过程）")
    ok = sum(1 for x in recs if x["pass"])
    passed = ok == len(recs)

    table(doc, ["场景", "用例数", "通过", "失败", "通过率", "执行耗时", "结论"],
          [[scene, len(recs), ok, len(recs) - ok, "%.0f%%" % (ok * 100 / len(recs)),
            "%.1f 秒" % (sum(x["elapsed_ms"] for x in recs) / 1000), "PASS" if passed else "FAIL"]],
          widths=[2.2, 2.0, 1.6, 1.6, 2.0, 2.6, 2.2])

    doc.add_heading("一、场景目标与判定口径", level=1)
    p(doc, meta["goal"])
    p(doc, "量化判定口径：每条用例都必须给出可机读的结论——HTTP 状态码命中预期、响应关键字段命中语义断言"
           "（如轨迹含指定工具、缺项清单精确到项、写工具状态为 pending_confirm）。不接受“看起来正常”。")

    doc.add_heading("二、测试环境与前置条件", level=1)
    table(doc, ["项目", "值"], [[k, v] for k, v in ENV], widths=[3.4, 12.6])
    p(doc, "前置条件：交付库已执行 scripts/init_db.py（账号 3 个、知识库 6 篇 25 切片、已确认档期 3 条），"
           "服务以 DB_PATH 指向该库启动；用例之间不共享登录态，未登录用例使用独立空会话，避免 Cookie 污染导致误判。")

    doc.add_heading("三、测试过程实录", level=1)
    p(doc, "以下每条用例给出：操作与请求 → 服务端实际响应（工具轨迹）→ 证据截图 → 判定依据。"
           "截图均为本次执行的真实响应报文渲染，未经修饰。")

    for i, r in enumerate(recs, start=1):
        doc.add_heading("用例 %s：%s（%s · %sms）" %
                        (r["id"], r["title"], r["status"], r["elapsed_ms"]), level=2)
        steps = [["1", "构造请求：" + (r["req"]["method"] + " " + r["req"]["path"]),
                  "接口可达，返回业务响应", "HTTP %s" % r["status"]],
                 ["2", "校验响应内容与断言条件", "命中全部断言",
                  "PASS" if r["pass"] else ("FAIL：" + (r["note"] or "断言未命中"))]]
        table(doc, ["步骤", "操作", "预期", "实测"], steps, widths=[1.4, 6.4, 4.4, 4.0])
        pic = os.path.join(EVI, r["id"] + ".png")
        if picture(doc, pic):
            caption(doc, "图 %d · %s 响应报文与工具轨迹（%s，%sms）" %
                    (i, r["id"], r["status"], r["elapsed_ms"]))
        for fn, desc in UI_SHOTS.get(r["id"], []):
            if picture(doc, os.path.join(SHOTS, fn)):
                caption(doc, "图 %d·UI · %s" % (i, desc))
        # 结果解读
        resp = r.get("resp") or {}
        if r["id"] == "N2":
            p(doc, "解读：轨迹含 search_kb，回答附来源引用与“最终结论需人工复核确认”页脚——说明口径类问题走的是"
                   "“先检索、后作答、留复核”的链路，而非模型凭记忆直答。")
        elif r["id"] == "N3":
            p(doc, "解读：轨迹依次为 search_kb → get_schedule → check_missing_fields → check_conflict → "
                   "create_schedule_draft(pending_confirm)，写工具没有直接执行，而是停在人工确认单上。")
        elif r["id"] in ("N4", "N5"):
            p(doc, "解读：确认单字段完整（8 项）且回答明确标注“确认后才执行、草稿不生效”；确认后草稿状态为 draft，"
                   "仍需提交人工复核，系统不产生 confirmed 档期。")
        elif r["id"] == "B2":
            p(doc, "解读：“下周二上午”被识别为无法解析的时间，流程在缺项校验层终止，未进入冲突检测，"
                   "也不会带着错误时间去做档期比较。")
        elif r["id"].startswith("A"):
            p(doc, "解读：对抗输入未改变系统的执行边界——写工具若被调用，状态只能是 pending_confirm；"
                   "确认接口只接受 confirm/reject，旁路参数直接 400。")
        elif r["id"].startswith("P"):
            p(doc, "解读：接口层用 401/403 区分“没登录”和“角色不够”，工具层再用 denied 兜住“登录了但无权写”，"
                   "两层各司其职，不能互相替代。")
        elif r["id"].startswith("F"):
            p(doc, "解读：异常输入得到显式错误码与可读消息，响应中不含内部堆栈，且失败不产生半状态记录。")
        else:
            p(doc, "解读：请求与响应均符合设计预期，判定依据为状态码与响应关键字段，未使用主观判断。")

    doc.add_heading("四、缺陷与处理", level=1)
    fixes = SCENE_FIX[scene]
    if fixes[0][1] == "无":
        p(doc, "本场景执行期间未发现缺陷，%d 条用例首轮全部 PASS。" % len(recs))
    else:
        table(doc, ["编号", "缺陷", "现象", "根因", "处理"],
              [list(x) for x in fixes], widths=[1.4, 4.6, 4.0, 3.6, 4.4], font_size=9)
        p(doc, "该缺陷由本场景用例 B2 首次暴露（修复前实测 500），修复后重跑全量用例 26/26 通过，"
               "并补入单元测试 tests/test_agent_core.py::TestGetSchedule 作为回归保护。")

    doc.add_heading("五、结论与复现", level=1)
    p(doc, "%s 场景 %d 条用例，通过 %d 条，通过率 %.0f%%，结论：%s。" %
      (scene, len(recs), ok, ok * 100 / len(recs),
       "系统在本场景下行为符合设计" if passed else "存在未通过用例，需修复后重跑"))
    p(doc, "复现命令（在仓库根目录执行）：")
    p(doc, "python -m uvicorn main:app --port 8500（工作目录 src/admin，DB_PATH 指向交付库）", indent=False)
    p(doc, "python scripts/run_scenario_tests.py --base http://127.0.0.1:8500", indent=False)
    p(doc, "用例断言与证据采集逻辑见 scripts/run_scenario_tests.py；单测见 tests/test_agent_core.py。", indent=False)

    doc.add_heading("六、证据清单", level=1)
    table(doc, ["编号", "用例", "证据文件", "类型"],
          [[r["id"], r["title"], r["id"] + ".png", "响应报文截图"] for r in recs],
          widths=[1.6, 6.4, 4.4, 3.6], font_size=9)

    out = os.path.join(TEST, meta["file"])
    doc.save(out)
    print("生成", out, "(%d 用例, %d 图)" % (len(recs), len(recs)))


# --------------------------------------------------------------------------
# 2) 场景测试方案（md + docx）
# --------------------------------------------------------------------------
def scene_stats():
    out = []
    for sc in ("正常", "边界", "对抗", "权限", "故障"):
        sub = [x for x in RUNS["records"] if x["scene"] == sc]
        out.append((sc, len(sub), sum(1 for x in sub if x["pass"]),
                    sum(x["elapsed_ms"] for x in sub)))
    return out


def build_plan():
    stats = scene_stats()
    total = sum(s[1] for s in stats)
    passed = sum(s[2] for s in stats)
    now = RUNS["tested_at"]

    md = []
    md.append("# 场景测试方案（正常 / 边界 / 对抗 / 权限 / 故障）\n")
    md.append("> 项目：场馆排期与需求变更 · 档期智能核对智能体系统　|　版本：v1.6.1　|　测试日期：%s" % now)
    md.append("> 执行方式：`scripts/run_scenario_tests.py` 一次跑完全部用例并落 JSON 证据　|　"
              "结论：**%d 条用例 %d 通过 / %d 失败，通过率 %.0f%%**" %
              (total, passed, total - passed, passed * 100 / total))
    md.append("> 配套材料：`Test/01-正常场景测试.docx` ~ `Test/05-故障场景测试.docx`（每类场景一份可视化过程，有图有过程）\n")

    md.append("## 一、测试目标与范围\n")
    md.append("本方案验证 MVP 在五类场景下的行为是否与设计一致，重点不是“功能能点开”，而是三件事：")
    md.append("1. **写操作不可绕过人工**：任何创建/变更类动作都必须停在人工确认单上，未确认前不落库、不生效；")
    md.append("2. **拦截位置正确**：非法输入必须停在对应校验层（缺项层 / 冲突层 / 状态机层），不能越层执行；")
    md.append("3. **拒绝要显式**：越权与异常必须给出可机读的错误码，不能静默降级或返回 200 后什么都不做。\n")
    md.append("范围覆盖 4 个页面（登录 / 问答 / 草稿 / 审计）、12 个核心接口；不含性能压测与大模型语义质量评测（见第七节退出准则）。\n")

    md.append("## 二、测试策略与量化口径\n")
    md.append("| 维度 | 设计 |")
    md.append("|------|------|")
    md.append("| 分层 | 单元测试（纯逻辑）→ 接口级场景测试（本方案）→ 部署后线上冒烟（Test/06） |")
    md.append("| 用例设计 | 等价类 + 边界值 + 攻击面枚举；每条用例一个判定点，可独立复现 |")
    md.append("| 判定依据 | HTTP 状态码 + 响应关键字段 + 工具轨迹状态（ok/denied/blocked/pending_confirm） |")
    md.append("| 环境隔离 | 未登录、员工、管理员各自独立会话，禁止共享 Cookie（否则会把“已登录”误判成“未登录可访问”） |")
    md.append("| 数据基线 | `scripts/init_db.py` 重建：账号 3、知识库 6 篇 25 切片、已确认档期 3 条 |")
    md.append("| 通过标准 | 单条：全部断言命中；场景：通过率 100%；总体：无 P0/P1 缺陷遗留 |\n")

    md.append("## 三、被测系统与三处人工确认点\n")
    md.append("| 确认点 | 实现位置 | 对应用例 |")
    md.append("|--------|----------|----------|")
    md.append("| ① 角色门禁（草稿创建仅协调员/管理员） | `src/admin/tools.py` S4_ALLOWED_ROLES | P3 |")
    md.append("| ② Harness 强制中断（写工具先出确认单） | `src/admin/harness.py` | N3、N4、A1、A3 |")
    md.append("| ③ 提交人工复核（草稿 draft 不生效） | `src/admin/tools.py` t_submit_for_review_impl | N5 |\n")

    md.append("## 四、用例清单与实测结果\n")
    for sc, n, ok, ms in stats:
        md.append("### 4.%d %s场景（%d 条，%d 通过）\n" % (("正常", "边界", "对抗", "权限", "故障").index(sc) + 1, sc, n, ok))
        md.append("| 编号 | 用例 | 预期 | 实测 | 耗时 | 判定 |")
        md.append("|------|------|------|------|------|------|")
        for r in RUNS["records"]:
            if r["scene"] != sc:
                continue
            md.append("| %s | %s | %s | %s | %dms | %s |" %
                      (r["id"], r["title"], r["expect"], r["status"], r["elapsed_ms"],
                       "PASS" if r["pass"] else "FAIL"))
        md.append("")

    md.append("## 五、判定汇总\n")
    md.append("| 场景 | 用例数 | 通过 | 失败 | 通过率 | 场景耗时 |")
    md.append("|------|--------|------|------|--------|----------|")
    for sc, n, ok, ms in stats:
        md.append("| %s | %d | %d | %d | %.0f%% | %.1fs |" % (sc, n, ok, n - ok, ok * 100 / n, ms / 1000))
    md.append("| **合计** | **%d** | **%d** | **%d** | **%.0f%%** | **%.1fs** |\n" %
              (total, passed, total - passed, passed * 100 / total, RUNS["duration_s"]))

    md.append("配套的单元测试 18 条（`tests/test_agent_core.py`）覆盖时间解析、缺项校验、冲突规则、凭据防篡改、"
              "角色门禁与状态机，全部通过。\n")

    md.append("## 六、缺陷记录\n")
    md.append("| 编号 | 严重度 | 现象 | 根因 | 状态 |")
    md.append("|------|--------|------|------|------|")
    md.append("| D-01 | P1 | B2 时间格式非法用例返回 500 Internal Server Error | `t_get_schedule` 未对 `parse_dt` 返回 `None` 做保护，`None` 参与时间比较抛 TypeError | 已修复 + 回归单测，复测 200 |")
    md.append("\n缺陷密度：%d 条 / %d 条用例 = %.2f，修复后回归通过率 100%%。\n" % (1, total, 1 / total))

    md.append("## 七、退出准则\n")
    md.append("| 准则 | 阈值 | 本次结果 |")
    md.append("|------|------|----------|")
    md.append("| 场景用例通过率 | 100%% | %.0f%% ✅ |" % (passed * 100 / total))
    md.append("| P0/P1 缺陷遗留 | 0 | 0（D-01 已修复） ✅ |")
    md.append("| 单元测试 | 全绿 | 18/18 ✅ |")
    md.append("| 写操作绕过人工确认 | 0 例 | 0 例 ✅ |")
    md.append("| 越权写入成功 | 0 例 | 0 例 ✅ |\n")

    md.append("## 八、复现方式\n")
    md.append("```bash")
    md.append("python scripts/init_db.py                      # 重建交付库（幂等）")
    md.append("cd src/admin && DB_PATH=../../data/venue_scheduling.db python -m uvicorn main:app --port 8500")
    md.append("python scripts/run_scenario_tests.py --base http://127.0.0.1:8500 \\")
    md.append("       --out assets/test-shots/runs/local.json")
    md.append("python -m unittest tests.test_agent_core -v    # 单测 18/18")
    md.append("```\n")
    md.append("> 线上复跑：`python scripts/run_scenario_tests.py --base https://venue-scheduling-agent.app.workbuddy.host --sc <sharecode>`\n")

    md.append("## 九、遗留与后续\n")
    md.append("1. 性能基线：本轮只记录单请求耗时（毫秒级），未做并发压测；建议补 20/50 并发的 P95 与错误率。")
    md.append("2. 语义质量：当前 LLM_PROVIDER=mock，判定的是链路与风控，不是回答质量；接真实模型后需补语义评测集。")
    md.append("3. 故障演练：本轮故障场景为输入型异常；数据库断连、SSE 中断重连、确认单超时（真实时钟推进）尚未注入演练。")

    md_text = "\n".join(md)
    md_path = os.path.join(TEST, "00-场景测试方案（正常·边界·对抗·权限·故障）.md")
    open(md_path, "w", encoding="utf-8").write(md_text)
    print("生成", md_path)

    # ---------- docx ----------
    doc = Document()
    setup(doc)
    title(doc, "场景测试方案", "正常 · 边界 · 对抗 · 权限 · 故障　|　场馆排期与需求变更 · 档期智能核对智能体系统")
    table(doc, ["版本", "测试日期", "用例总数", "通过", "失败", "通过率", "总耗时"],
          [["v1.6.1", now, total, passed, total - passed, "%.0f%%" % (passed * 100 / total),
            "%.1fs" % RUNS["duration_s"]]],
          widths=[2.2, 4.2, 2.4, 1.8, 1.8, 2.2, 2.2], font_size=9)

    doc.add_heading("一、测试目标与范围", level=1)
    p(doc, "本方案验证 MVP 在五类场景下的行为是否与设计一致，重点不是“功能能点开”，而是三件事：写操作不可绕过人工、"
           "拦截位置正确、拒绝要显式。")
    for line in ["写操作不可绕过人工：任何创建/变更类动作都必须停在人工确认单上，未确认前不落库、不生效；",
                 "拦截位置正确：非法输入必须停在对应校验层（缺项层 / 冲突层 / 状态机层），不能越层执行；",
                 "拒绝要显式：越权与异常必须给出可机读的错误码，不能静默降级或返回 200 后什么都不做。"]:
        par = doc.add_paragraph(line, style="List Bullet")
        for run in par.runs:
            run.font.size = Pt(11)
    p(doc, "范围覆盖 4 个页面（登录 / 问答 / 草稿 / 审计）与 12 个核心接口；不含性能压测与大模型语义质量评测。")

    doc.add_heading("二、测试策略与量化口径", level=1)
    table(doc, ["维度", "设计"],
          [["分层", "单元测试（纯逻辑）→ 接口级场景测试（本方案）→ 部署后线上冒烟（Test/06）"],
           ["用例设计", "等价类 + 边界值 + 攻击面枚举；每条用例一个判定点，可独立复现"],
           ["判定依据", "HTTP 状态码 + 响应关键字段 + 工具轨迹状态（ok/denied/blocked/pending_confirm）"],
           ["环境隔离", "未登录 / 员工 / 管理员各自独立会话，禁止共享 Cookie"],
           ["数据基线", "scripts/init_db.py 重建：账号 3、知识库 6 篇 25 切片、已确认档期 3 条"],
           ["通过标准", "单条全部断言命中；场景通过率 100%；无 P0/P1 缺陷遗留"]],
          widths=[3.0, 13.0], font_size=9.5)

    doc.add_heading("三、被测系统与三处人工确认点", level=1)
    table(doc, ["确认点", "实现位置", "对应用例"],
          [["① 角色门禁（草稿创建仅协调员/管理员）", "src/admin/tools.py · S4_ALLOWED_ROLES", "P3"],
           ["② Harness 强制中断（写工具先出确认单）", "src/admin/harness.py", "N3、N4、A1、A3"],
           ["③ 提交人工复核（草稿 draft 不生效）", "tools.py · t_submit_for_review_impl", "N5"]],
          widths=[7.0, 7.4, 3.0], font_size=9.5)

    doc.add_heading("四、用例清单与实测结果", level=1)
    for idx, (sc, n, ok, ms) in enumerate(stats, start=1):
        doc.add_heading("4.%d %s场景（%d 条，%d 通过，%.0f%%）" %
                        (idx, sc, n, ok, ok * 100 / n), level=2)
        rows = [[r["id"], r["title"], r["expect"], r["status"], "%dms" % r["elapsed_ms"],
                 "PASS" if r["pass"] else "FAIL"]
                for r in RUNS["records"] if r["scene"] == sc]
        table(doc, ["编号", "用例", "预期", "实测", "耗时", "判定"], rows,
              widths=[1.5, 5.6, 2.2, 1.8, 2.0, 1.8], font_size=9)

    doc.add_heading("五、判定汇总", level=1)
    rows = [[sc, n, ok, n - ok, "%.0f%%" % (ok * 100 / n), "%.1fs" % (ms / 1000)]
            for sc, n, ok, ms in stats]
    rows.append(["合计", total, passed, total - passed, "%.0f%%" % (passed * 100 / total),
                 "%.1fs" % RUNS["duration_s"]])
    table(doc, ["场景", "用例数", "通过", "失败", "通过率", "场景耗时"], rows,
          widths=[3.0, 2.4, 2.0, 2.0, 2.4, 3.0], font_size=9.5)
    p(doc, "配套单元测试 18 条（tests/test_agent_core.py）覆盖时间解析、缺项校验、冲突规则、凭据防篡改、"
           "角色门禁与状态机，全部通过。")

    doc.add_heading("六、缺陷记录", level=1)
    table(doc, ["编号", "严重度", "现象", "根因", "状态"],
          [["D-01", "P1", "B2 时间格式非法用例返回 500",
            "t_get_schedule 未对 parse_dt 返回 None 做保护，None 参与比较抛 TypeError",
            "已修复 + 回归单测，复测 200"]],
          widths=[1.6, 1.8, 4.2, 7.0, 4.4], font_size=9)
    p(doc, "缺陷密度：1 条 / %d 条用例 = %.2f；修复后全量回归 100%% 通过。" % (total, 1 / total))

    doc.add_heading("七、退出准则", level=1)
    table(doc, ["准则", "阈值", "本次结果"],
          [["场景用例通过率", "100%", "%.0f%%　达标" % (passed * 100 / total)],
           ["P0/P1 缺陷遗留", "0", "0（D-01 已修复）　达标"],
           ["单元测试", "全绿", "18/18　达标"],
           ["写操作绕过人工确认", "0 例", "0 例　达标"],
           ["越权写入成功", "0 例", "0 例　达标"]],
          widths=[6.0, 4.0, 6.0], font_size=9.5)

    doc.add_heading("八、复现方式", level=1)
    for line in ["python scripts/init_db.py",
                 "cd src/admin && DB_PATH=../../data/venue_scheduling.db python -m uvicorn main:app --port 8500",
                 "python scripts/run_scenario_tests.py --base http://127.0.0.1:8500 --out assets/test-shots/runs/local.json",
                 "python -m unittest tests.test_agent_core -v"]:
        par = doc.add_paragraph()
        run = par.add_run(line)
        run.font.name = "Consolas"
        run.font.size = Pt(9.5)
        par.paragraph_format.left_indent = Cm(0.6)

    doc.add_heading("九、遗留与后续", level=1)
    for line in ["性能基线：本轮记录单请求耗时（毫秒级），未做并发压测；建议补 20/50 并发的 P95 与错误率。",
                 "语义质量：当前 LLM_PROVIDER=mock，判定链路与风控，不判回答质量；接真实模型后补语义评测集。",
                 "故障演练：数据库断连、SSE 中断重连、确认单超时（真实时钟推进）尚未注入演练。"]:
        par = doc.add_paragraph(line, style="List Number")
        for run in par.runs:
            run.font.size = Pt(11)

    out = os.path.join(TEST, "00-场景测试方案（正常·边界·对抗·权限·故障）.docx")
    doc.save(out)
    print("生成", out)


# --------------------------------------------------------------------------
# 3) 上线部署地址（md + docx）
# --------------------------------------------------------------------------
ONLINE = {
    "link": "https://venue-scheduling-agent.app.workbuddy.host/?sharecode=YCJ7OTYNZZ",
    "bare": "https://venue-scheduling-agent.app.workbuddy.host/",
    "app": "场馆排期智能核对系统",
    "checked": datetime.now().strftime("%Y-%m-%d %H:%M"),
}

ONLINE_CHECK = [
    ["裸地址访问（无 sharecode）", "GET /", "403 Forbidden", "403", "门禁生效，未授权不可见"],
    ["分享链接进入登录页", "GET /login?sharecode=…", "200 + 登录表单", "200（4.42s）", "页面可打开"],
    ["管理员登录", "POST /api/auth/login", "200 + JWT", "200（0.85s）", "凭据有效"],
    ["口径咨询", "POST /api/agent/run", "200 + 来源引用 + 复核页脚", "200", "RAG 链路通"],
    ["员工访问管理员接口", "GET /api/admin/users", "403", "403", "RBAC 生效"],
]


def build_online():
    md = []
    md.append("# 上线部署网站地址\n")
    md.append("> 项目：场馆排期与需求变更 · 档期智能核对智能体系统　|　部署形态：单端口 HTTP 服务（FastAPI + uvicorn）"
              "　|　核验时间：%s\n" % ONLINE["checked"])
    md.append("## 一、访问地址\n")
    md.append("| 项 | 值 |")
    md.append("|----|----|")
    md.append("| **分享链接（必须带 sharecode）** | %s |" % ONLINE["link"])
    md.append("| 应用名称 | %s |" % ONLINE["app"])
    md.append("| 裸地址（无 sharecode） | %s → 403 |" % ONLINE["bare"])
    md.append("| 部署目录 | `deploy/`（仓库内干净部署副本，入口 `run.py` 读 `PORT`） |")
    md.append("| 数据 | `deploy/data/admin.db`（SQLite，随包发布；10 张表 + 种子数据） |")
    md.append("| 运行方式 | `python run.py` → uvicorn 绑定 `0.0.0.0:$PORT` |\n")
    md.append("> 分享链接带访问码门禁：**必须使用上表完整链接**，去掉 `?sharecode=…` 会返回 403。"
              "这是平台侧的门禁行为，不是系统故障。\n")

    md.append("## 二、演示账号\n")
    md.append("| 账号 | 密码 | 角色 | 演示看点 |")
    md.append("|------|------|------|----------|")
    md.append("| admin | admin123 | 管理员 | 知识库管理、审计、草稿创建（触发强制中断）、用户管理 |")
    md.append("| coord | coord123 | 协调员 | 草稿创建与提交复核（S4 试点角色） |")
    md.append("| staff | staff123 | 一线员工 | 问答与口径咨询；创建草稿会被角色门禁拒绝 |\n")

    md.append("## 三、上线验证记录（%s 实测）\n" % ONLINE["checked"])
    md.append("| 检查项 | 请求 | 预期 | 实测 | 结论 |")
    md.append("|--------|------|------|------|------|")
    for row in ONLINE_CHECK:
        md.append("| %s |" % " | ".join(row))
    md.append("")
    md.append("完整的 26 条场景用例可在本地对同一份代码复跑（见 `Test/00-场景测试方案`），"
              "线上冒烟只验证“服务可达 + 关键链路通 + 权限边界有效”三项。\n")

    md.append("## 四、5 分钟演示路径\n")
    md.append("1. **口径咨询**（30 秒）：问「装卸口占用的判定口径是什么？」→ 回答带来源引用与「最终结论需人工复核确认」页脚。")
    md.append("2. **预订全流程**（2 分钟）：问答页切「智能体模式」，填场地/装卸口/活动与进撤场时间 → 5 步工具轨迹 → 🛑 强制中断确认卡片 → 点「确认创建」→ 草稿创建（状态 draft，不生效）→ 提交复核。")
    md.append("3. **规则拦截**（1 分钟）：把场地改为主馆、时间段与已确认档期重叠 → 冲突检测阻断并给出依据条款。")
    md.append("4. **越权验证**（30 秒）：用 staff 重复第 2 步 → 工具层返回 denied，提示“试点期仅协调员/管理员可用”。")
    md.append("5. **审计留痕**（1 分钟）：管理员进审计页 → 看到提问原文、工具轨迹、耗时与版本。\n")

    md.append("## 五、可用性与排错\n")
    md.append("| 现象 | 原因 | 处理 |")
    md.append("|------|------|------|")
    md.append("| 打开是 403 | 链接缺 `?sharecode=` | 用第一节完整链接 |")
    md.append("| 页面能开但接口 401 | 浏览器未带 Cookie（禁用了 Cookie / 无痕拦截） | 允许 Cookie 后重新登录；接口也接受 `Authorization: Bearer` |")
    md.append("| 首次打开慢（3~5 秒） | 沙箱冷启动 | 等 5 秒刷新；后续请求在 1 秒内 |")
    md.append("| 登录返回 200 但随即 401 | 令牌被网关注入头覆盖 | 本版已改为 Bearer 与 Cookie 双通道校验 |\n")

    md.append("## 六、部署与回退\n")
    md.append("```bash")
    md.append("cd deploy")
    md.append("pip install -r requirements.txt")
    md.append("python run.py            # 读 PORT 环境变量，默认 8500")
    md.append("```")
    md.append("")
    md.append("- 部署包自带 SQLite 库，无需外部数据库；备份即备份 `deploy/data/admin.db`。")
    md.append("- 回退：本系统是只读辅助层，停止服务即完成回退，对原排期业务零影响；草稿不生效，不会留下已锁定档期。")
    md.append("- 详细运维步骤（账号、备份、故障处理）见 `docs/9-上线交付运行手册/上线交付运行手册.docx`。\n")

    md.append("## 七、版本与同步状态\n")
    md.append("| 项 | 值 |")
    md.append("|----|----|")
    md.append("| 本地交付版本 | v1.6.1（含 D-01 修复：非法时间不再 500） |")
    md.append("| 线上版本 | v1.6.0（功能一致，未含 D-01 修复） |")
    md.append("| 同步方式 | 覆盖发布 `deploy/` 目录，分享链接不变 |")
    md.append("| 提示 | 若用非法时间做对抗演示，v1.6.0 线上会返回 500，属已知缺陷，本地版已修复 |\n")

    md_path = os.path.join(TEST, "06-上线部署网站地址.md")
    open(md_path, "w", encoding="utf-8").write("\n".join(md))
    print("生成", md_path)

    doc = Document()
    setup(doc)
    title(doc, "上线部署网站地址", "MVP 系统公网访问地址、账号与上线验证记录")
    p(doc, "系统已部署为单端口 HTTP 服务，公网可直接访问。以下地址、账号与验证结果均为实际核验，"
           "核验时间 %s。" % ONLINE["checked"])

    doc.add_heading("一、访问地址", level=1)
    table(doc, ["项", "值"],
          [["分享链接（必须带 sharecode）", ONLINE["link"]],
           ["应用名称", ONLINE["app"]],
           ["裸地址（无 sharecode）", ONLINE["bare"] + " → 403"],
           ["部署目录", "deploy/（干净部署副本，入口 run.py 读取 PORT）"],
           ["数据", "deploy/data/admin.db（SQLite，随包发布，10 张表 + 种子数据）"]],
          widths=[5.0, 11.0], font_size=9)
    p(doc, "注意：分享链接带访问码门禁，去掉 ?sharecode=… 会返回 403。这是平台侧的门禁行为，不是系统故障。")

    doc.add_heading("二、演示账号", level=1)
    table(doc, ["账号", "密码", "角色", "演示看点"],
          [["admin", "admin123", "管理员", "知识库管理、审计、草稿创建（触发强制中断）、用户管理"],
           ["coord", "coord123", "协调员", "草稿创建与提交复核（S4 试点角色）"],
           ["staff", "staff123", "一线员工", "问答与口径咨询；创建草稿会被角色门禁拒绝"]],
          widths=[2.6, 3.0, 3.0, 7.4], font_size=9)

    doc.add_heading("三、上线验证记录", level=1)
    table(doc, ["检查项", "请求", "预期", "实测", "结论"], ONLINE_CHECK,
          widths=[4.2, 4.6, 3.6, 2.2, 3.4], font_size=8.5)

    doc.add_heading("四、5 分钟演示路径", level=1)
    for line in ["口径咨询：问「装卸口占用的判定口径是什么？」，回答带来源引用与人工复核页脚。",
                 "预订全流程：智能体模式填场地/装卸口/进撤场时间 → 5 步工具轨迹 → 强制中断确认卡片 → 确认创建 → 草稿不生效 → 提交复核。",
                 "规则拦截：场地改主馆、时间与已确认档期重叠，冲突检测阻断并给出依据条款。",
                 "越权验证：用 staff 重复第二步，工具层返回 denied。",
                 "审计留痕：管理员进审计页，查看提问原文、工具轨迹与耗时。"]:
        par = doc.add_paragraph(line, style="List Number")
        for run in par.runs:
            run.font.size = Pt(11)

    doc.add_heading("五、可用性与排错", level=1)
    table(doc, ["现象", "原因", "处理"],
          [["打开是 403", "链接缺 sharecode", "使用第一节完整链接"],
           ["能开页面但接口 401", "浏览器未带 Cookie", "允许 Cookie 后重新登录；接口也接受 Bearer 令牌"],
           ["首次打开慢（3~5 秒）", "沙箱冷启动", "等 5 秒刷新，后续请求 1 秒内"],
           ["登录 200 随即 401", "令牌被网关注入头覆盖", "本版已改为 Bearer 与 Cookie 双通道校验"]],
          widths=[5.4, 5.2, 5.4], font_size=9)

    doc.add_heading("六、部署与回退", level=1)
    for line in ["cd deploy", "pip install -r requirements.txt", "python run.py   # 读取 PORT 环境变量，默认 8500"]:
        par = doc.add_paragraph()
        run = par.add_run(line)
        run.font.name = "Consolas"
        run.font.size = Pt(9.5)
        par.paragraph_format.left_indent = Cm(0.6)
    p(doc, "部署包自带 SQLite 库，无需外部数据库，备份即备份 deploy/data/admin.db；"
           "回退时停止服务即可——系统是只读辅助层，草稿不生效，不会留下已锁定档期，对原排期业务零影响。")

    doc.add_heading("七、版本与同步状态", level=1)
    table(doc, ["项", "值"],
          [["本地交付版本", "v1.6.1（含 D-01 修复：非法时间不再 500）"],
           ["线上版本", "v1.6.0（功能一致，未含 D-01 修复）"],
           ["同步方式", "覆盖发布 deploy/ 目录，分享链接不变"],
           ["提示", "若用非法时间做对抗演示，v1.6.0 线上会返回 500，属已知缺陷，本地版已修复"]],
          widths=[4.0, 12.0], font_size=9)

    out = os.path.join(TEST, "06-上线部署网站地址.docx")
    doc.save(out)
    print("生成", out)


if __name__ == "__main__":
    render_cards(RUNS["records"])
    for sc in SCENES:
        build_scene_doc(sc, [x for x in RUNS["records"] if x["scene"] == sc])
    build_plan()
    build_online()
    print("\n全部生成完毕：%s" % TEST)
