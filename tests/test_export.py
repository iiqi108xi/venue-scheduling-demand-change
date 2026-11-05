# -*- coding: utf-8 -*-
"""导出与统计模块单元测试（export.py）。

覆盖：Markdown 交接单、ICS 日历（含非法时间）、冲突报告、审计统计聚合。
全部使用 dict / 假对象，不依赖数据库与网络。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "admin"))

from export import audit_stats, conflict_report_markdown, draft_to_ics, draft_to_markdown  # noqa: E402

SAMPLE_DRAFT = {
    "id": 6,
    "project_name": "演示展",
    "organizer": "华辰会展",
    "venue": "副馆 Hall-B",
    "event_start": "2026-12-30 09:00",
    "event_end": "2026-12-30 16:00",
    "dock_no": "D",
    "load_in_start": "2026-12-30 06:00",
    "load_out_end": "2026-12-30 18:00",
    "username": "coord",
    "status": "draft",
    "note": "",
}


class TestDraftMarkdown(unittest.TestCase):
    def test_contains_all_fields(self):
        md = draft_to_markdown(SAMPLE_DRAFT)
        for text in ("演示展", "副馆 Hall-B", "2026-12-30 09:00 ~ 2026-12-30 16:00",
                     "装卸口 | D", "华辰会展", "草稿，不生效"):
            self.assertIn(text, md)

    def test_has_confirmation_checklist(self):
        md = draft_to_markdown(SAMPLE_DRAFT)
        self.assertIn("- [ ]", md)  # 交接确认清单
        self.assertIn("不触发锁定/签约", md)

    def test_missing_fields_fallback(self):
        md = draft_to_markdown({"project_name": "只有名字"})
        self.assertIn("只有名字", md)
        self.assertIn("—", md)


class TestDraftIcs(unittest.TestCase):
    def test_structure_and_tentative(self):
        ics = draft_to_ics(SAMPLE_DRAFT)
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR"))
        self.assertTrue(ics.rstrip().endswith("END:VCALENDAR"))
        self.assertEqual(ics.count("BEGIN:VEVENT"), 2)  # 主体 + 进撤场
        self.assertEqual(ics.count("STATUS:TENTATIVE"), 2)
        self.assertIn("【草稿·不生效】演示展", ics)
        self.assertIn("DTSTART:20261230T090000", ics)
        self.assertIn("DTEND:20261230T160000", ics)

    def test_crlf_line_endings(self):
        ics = draft_to_ics(SAMPLE_DRAFT)
        self.assertIn("\r\n", ics)

    def test_invalid_time_raises(self):
        bad = dict(SAMPLE_DRAFT, event_start="not-a-time")
        with self.assertRaises(ValueError):
            draft_to_ics(bad)

    def test_special_chars_escaped(self):
        tricky = dict(SAMPLE_DRAFT, project_name="A;B,C\nD")
        ics = draft_to_ics(tricky)
        self.assertIn("A\\;B\\,C\\nD", ics)


class TestConflictReport(unittest.TestCase):
    def test_with_conflicts(self):
        conflicts = [
            {"type": "time_overlap", "detail": "与 A001 时间窗重叠", "basis": "业务规范 1.2", "blocking": True},
            {"type": "dock_conflict", "detail": "装卸口 B 被占", "basis": "业务规范 2.2", "blocking": True},
        ]
        rpt = conflict_report_markdown(SAMPLE_DRAFT, conflicts)
        self.assertIn("冲突候选（共 2 条）", rpt)
        self.assertIn("[time_overlap] （阻断）", rpt)
        self.assertIn("业务规范 1.2", rpt)
        self.assertIn("人工复核确认", rpt)

    def test_without_conflicts(self):
        rpt = conflict_report_markdown(SAMPLE_DRAFT, [])
        self.assertIn("未检出硬冲突", rpt)


class _FakeLog:
    """模拟 AgentRunLog 行对象。"""

    def __init__(self, run_id: str, tool: str, status: str = "ok", ms: int = 100):
        self.run_id = run_id
        self.tool_name = tool
        self.status = status
        self.duration_ms = ms


class TestAuditStats(unittest.TestCase):
    def test_empty(self):
        s = audit_stats([])
        self.assertEqual(s["total_steps"], 0)
        self.assertEqual(s["total_runs"], 0)
        self.assertEqual(s["confirm_rate"], 0.0)

    def test_aggregation(self):
        logs = [
            _FakeLog("r1", "search_kb", "ok", 50),
            _FakeLog("r1", "check_conflict", "ok", 150),
            _FakeLog("r1", "create_schedule_draft", "pending_confirm", 10),
            _FakeLog("r2", "search_kb", "ok", 70),
            _FakeLog("r2", "get_schedule", "blocked", 20),
        ]
        s = audit_stats(logs)
        self.assertEqual(s["total_steps"], 5)
        self.assertEqual(s["total_runs"], 2)
        self.assertEqual(s["avg_rounds_per_run"], 2.5)
        self.assertEqual(s["by_tool"]["search_kb"], 2)
        self.assertEqual(s["by_status"]["blocked"], 1)
        self.assertEqual(s["by_status"]["pending_confirm"], 1)
        self.assertEqual(s["confirm_rate"], 0.2)
        self.assertEqual(s["avg_duration_by_tool"]["check_conflict"], 150)
        # 状态表按固定顺序输出（ok 在最前）
        self.assertEqual(list(s["by_status"].keys())[0], "ok")


if __name__ == "__main__":
    unittest.main()
