# -*- coding: utf-8 -*-
"""核心业务逻辑单元测试（16 条）。

覆盖：时间解析、缺项校验、冲突规则引擎、凭据签名/防篡改、角色门禁、状态机。
运行：.venv/Scripts/python.exe -m unittest tests.test_agent_core -v
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "admin"))

# 必须在导入 database 之前指定临时库，避免污染交付演示库
_TMP = os.path.join(tempfile.gettempdir(), "venue_unittest.db")
if os.path.exists(_TMP):
    os.remove(_TMP)
os.environ["DB_PATH"] = _TMP
os.environ.setdefault("LLM_PROVIDER", "mock")

import database  # noqa: E402
import tools  # noqa: E402
from database import ScheduleDraft, ScheduleSlot, SessionLocal  # noqa: E402


def _draft(**kw):
    d = {"project_name": "春季设备展", "organizer": "东浩会展", "venue": "副馆 Hall-B",
         "event_start": "2026-12-01 09:00", "event_end": "2026-12-03 17:00",
         "dock_no": "D12", "load_in_start": "2026-11-30 08:00",
         "load_out_end": "2026-12-04 12:00"}
    d.update(kw)
    return d


class TestParseDt(unittest.TestCase):
    def test_iso_format(self):
        self.assertEqual(tools.parse_dt("2026-12-01T09:00").hour, 9)

    def test_space_format(self):
        self.assertEqual(tools.parse_dt(" 2026-12-03 17:00 ").minute, 0)

    def test_chinese_text_is_none(self):
        self.assertIsNone(tools.parse_dt("下周二上午"))

    def test_empty_is_none(self):
        self.assertIsNone(tools.parse_dt(""))


class TestMissingFields(unittest.TestCase):
    def test_complete_issues_token(self):
        r = tools.check_missing_fields_impl(_draft())
        self.assertTrue(r["complete"])
        self.assertEqual(r["missing"], [])
        self.assertIn("precheck_token", r)

    def test_three_missing_fields(self):
        r = tools.check_missing_fields_impl(_draft(dock_no="", load_in_start="", load_out_end=""))
        self.assertFalse(r["complete"])
        self.assertEqual(len(r["missing"]), 3)

    def test_bad_time_reported(self):
        r = tools.check_missing_fields_impl(_draft(event_start="下周二上午"))
        self.assertFalse(r["complete"])
        self.assertTrue(any("时间格式无法解析" in m for m in r["missing"]))


class TestConflictEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.Base.metadata.create_all(database.engine)
        cls.db = SessionLocal()
        cls.db.query(ScheduleSlot).delete()
        cls.db.add(ScheduleSlot(request_no="SL-001", project_name="十月工业展", venue="副馆 Hall-B",
                                event_start="2026-12-02 09:00", event_end="2026-12-02 18:00",
                                dock_no="D12", load_in_start="2026-12-01 08:00",
                                load_out_end="2026-12-03 20:00", status="confirmed"))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_time_overlap_blocks(self):
        r = tools.check_conflict_impl(_draft(), self.db)
        self.assertTrue(r["blocking"])
        self.assertTrue(any(c["type"] == "time_overlap" for c in r["conflicts"]))

    def test_no_conflict_issues_token(self):
        r = tools.check_conflict_impl(_draft(venue="主馆 Hall-A",
                                             event_start="2027-03-01 09:00",
                                             event_end="2027-03-03 17:00",
                                             load_in_start="2027-02-28 08:00",
                                             load_out_end="2027-03-04 12:00"), self.db)
        self.assertFalse(r["blocking"])
        self.assertIn("precheck_token", r)

    def test_dock_conflict_detected(self):
        r = tools.check_conflict_impl(_draft(venue="主馆 Hall-A",
                                             event_start="2026-12-02 09:00",
                                             event_end="2026-12-02 10:00"), self.db)
        types = [c["type"] for c in r["conflicts"]]
        self.assertIn("dock_conflict", types)

    def test_short_load_is_warning_only(self):
        r = tools.check_conflict_impl(_draft(venue="多功能厅 Hall-C",
                                             event_start="2027-05-01 09:00",
                                             event_end="2027-05-02 17:00",
                                             load_in_start="2027-04-30 20:00",
                                             load_out_end="2027-05-01 04:00"), self.db)
        warn = [c for c in r["conflicts"] if c["type"] == "load_duration_violation"]
        self.assertTrue(warn)
        self.assertFalse(warn[0]["blocking"])


class TestGetSchedule(unittest.TestCase):
    """回归用例 D-01：非法日期范围不得抛 500（线上曾出现 TypeError）。"""

    @classmethod
    def setUpClass(cls):
        database.Base.metadata.create_all(database.engine)
        cls.db = SessionLocal()
        cls.db.query(ScheduleSlot).delete()
        cls.db.add(ScheduleSlot(request_no="SL-002", project_name="春季设备展", venue="副馆 Hall-B",
                                event_start="2026-12-01 09:00", event_end="2026-12-03 17:00",
                                dock_no="D12", status="confirmed"))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_bad_range_does_not_raise(self):
        r = tools.t_get_schedule(self.db, "副馆 Hall-B", "下周二上午~2026-12-03 17:00")
        self.assertIn("schedules", r)
        self.assertEqual(len(r["schedules"]), 1)   # 退化为不限，保守全返回

    def test_range_end_missing_does_not_raise(self):
        r = tools.t_get_schedule(self.db, "副馆 Hall-B", "2026-12-01 00:00~"
                                )
        self.assertIn("schedules", r)

    def test_normal_range_filters(self):
        r = tools.t_get_schedule(self.db, "副馆 Hall-B", "2027-01-01 00:00~2027-01-02 00:00")
        self.assertEqual(r["schedules"], [])


class TestTokensAndRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.Base.metadata.create_all(database.engine)
        cls.db = SessionLocal()
        cls.draft = _draft(venue="主馆 Hall-A", event_start="2027-08-01 09:00",
                           event_end="2027-08-02 17:00", load_in_start="2027-07-31 08:00",
                           load_out_end="2027-08-03 12:00")
        cls.miss = tools.check_missing_fields_impl(cls.draft)["precheck_token"]
        cls.conf = tools.check_conflict_impl(cls.draft, cls.db)["precheck_token"]

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_tampered_token_rejected(self):
        with self.assertRaises(tools.ToolDenied):
            tools.t_create_schedule_draft_impl(self.draft, self.miss + "x", self.conf,
                                               1, "admin", "admin", self.db)

    def test_staff_role_denied(self):
        with self.assertRaises(tools.ToolDenied):
            tools.t_create_schedule_draft_impl(self.draft, self.miss, self.conf,
                                               2, "staff", "staff", self.db)

    def test_admin_create_then_submit_then_repeat(self):
        r = tools.t_create_schedule_draft_impl(self.draft, self.miss, self.conf,
                                               1, "admin", "admin", self.db)
        self.assertEqual(r["status"], "draft")
        s = tools.t_submit_for_review_impl(r["draft_id"], "admin", "admin", self.db)
        self.assertEqual(s["status"], "pending_review")
        with self.assertRaises(tools.ToolError):
            tools.t_submit_for_review_impl(r["draft_id"], "admin", "admin", self.db)

    def test_draft_never_auto_confirmed(self):
        row = self.db.query(ScheduleDraft).first()
        self.assertNotEqual(row.status, "confirmed")


if __name__ == "__main__":
    unittest.main()
