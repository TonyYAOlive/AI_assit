"""计划生成器测试：mock LLM，验证 PlannedTask 和 DayPlan 记录生成。"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.planner as planner_module
from app.models import Base, Memo, PlannedTask, DayPlan
from app.services.planner import (
    generate_weekly_tasks,
    generate_day_plan,
    target_week_start,
    get_or_generate_weekly_plan,
    get_or_generate_day_plan,
    format_weekly_plan,
    format_day_plan,
)

# ── 测试数据库 ─────────────────────────────────────────────────────────────────

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_planner_db():
    """planner 测试用独立内存 DB，不影响 conftest 的 HTTP 测试 DB。"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _add_memo(db, content, memo_type="temporary", status="pending"):
    m = Memo(content=content, memo_type=memo_type, status=status)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _add_planned_task(db, title, duration_hrs=1.0, status="pending", week_start=None):
    if week_start is None:
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())
        week_start = datetime(week_start.year, week_start.month, week_start.day)
    t = PlannedTask(
        title=title,
        week_start_date=week_start,
        duration_hrs=duration_hrs,
        status=status,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ── generate_weekly_tasks 测试 ────────────────────────────────────────────────

class TestGenerateWeeklyTasks:
    def test_creates_planned_tasks_from_llm_output(self, db):
        _add_memo(db, "买牛奶", memo_type="temporary")
        _add_memo(db, "每周英语5小时", memo_type="long_term")

        mock_items = [
            {
                "title": "买牛奶",
                "description": "",
                "duration_hrs": 0.5,
                "category": "生活",
                "priority": "低",
                "source_type": "memo",
                "source_id": 1,
                "notes": "",
            },
            {
                "title": "英语学习",
                "description": "",
                "duration_hrs": 1.0,
                "category": "学习",
                "priority": "中",
                "source_type": "task",
                "source_id": 2,
                "notes": "每周5小时",
            },
        ]

        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            result = generate_weekly_tasks(db)

        assert len(result) == 2
        assert result[0]["title"] == "买牛奶"
        assert result[0]["duration_hrs"] == 0.5
        assert result[1]["title"] == "英语学习"
        assert result[1]["source_type"] == "task"

        # 验证写入了数据库
        tasks_in_db = db.query(PlannedTask).all()
        assert len(tasks_in_db) == 2

    def test_empty_memos_and_tasks_returns_empty(self, db):
        with patch.object(planner_module.llm_client, "chat_json", return_value=[]):
            result = generate_weekly_tasks(db)
        assert result == []
        assert db.query(PlannedTask).count() == 0

    def test_llm_non_list_response_returns_empty(self, db):
        _add_memo(db, "测试备忘")
        with patch.object(planner_module.llm_client, "chat_json", return_value={"error": "bad"}):
            result = generate_weekly_tasks(db)
        assert result == []

    def test_week_start_date_is_next_monday(self, db):
        _add_memo(db, "测试")
        mock_items = [{"title": "测试", "description": "", "duration_hrs": 1.0,
                       "category": "其他", "priority": "中",
                       "source_type": "memo", "source_id": 1, "notes": ""}]
        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            result = generate_weekly_tasks(db)

        task_in_db = db.query(PlannedTask).first()
        assert task_in_db.week_start_date.weekday() == 0  # 周一


# ── generate_day_plan 测试 ────────────────────────────────────────────────────

class TestGenerateDayPlan:
    # 固定一个工作日（周一）作为测试基准
    MOCK_WEEKDAY = datetime(2026, 6, 22, 10, 0)  # 周一
    MOCK_MONDAY = datetime(2026, 6, 22)           # 该周周一零点

    def test_weekday_adds_work_entry(self, db):
        _add_planned_task(db, "英语学习", week_start=self.MOCK_MONDAY)

        mock_items = [
            {
                "title": "英语学习",
                "start_time": "2026-06-22T15:30:00",
                "end_time": "2026-06-22T16:30:00",
                "category": "学习",
                "priority": "中",
                "planned_task_id": 1,
                "notes": "",
            }
        ]

        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            result = generate_day_plan(db, now=self.MOCK_WEEKDAY)

        work_entries = [e for e in result if e.get("is_work")]
        assert len(work_entries) == 1
        assert work_entries[0]["title"] == "工作"
        assert work_entries[0]["start_time"][11:16] == "07:00"

    def test_no_pending_tasks_returns_only_work_on_weekday(self, db):
        result = generate_day_plan(db, now=self.MOCK_WEEKDAY)
        assert len(result) == 1
        assert result[0]["is_work"] is True

    def test_day_plan_entries_written_to_db(self, db):
        _add_planned_task(db, "运动", week_start=self.MOCK_MONDAY)

        mock_items = [
            {
                "title": "运动",
                "start_time": "2026-06-22T16:00:00",
                "end_time": "2026-06-22T17:00:00",
                "category": "运动",
                "priority": "中",
                "planned_task_id": 1,
                "notes": "",
            }
        ]

        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            generate_day_plan(db, now=self.MOCK_WEEKDAY)

        non_work = [e for e in db.query(DayPlan).all() if not e.is_work]
        assert len(non_work) == 1
        assert non_work[0].title == "运动"

    def test_duration_minutes_calculated_correctly(self, db):
        _add_planned_task(db, "阅读", week_start=self.MOCK_MONDAY)

        mock_items = [
            {
                "title": "阅读",
                "start_time": "2026-06-22T15:00:00",
                "end_time": "2026-06-22T15:45:00",
                "category": "学习",
                "priority": "低",
                "planned_task_id": 1,
                "notes": "",
            }
        ]

        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            generate_day_plan(db, now=self.MOCK_WEEKDAY)

        plan = db.query(DayPlan).filter(DayPlan.title == "阅读").first()
        assert plan.duration_minutes == 45

    def test_invalid_time_format_skipped(self, db):
        _add_planned_task(db, "测试", week_start=self.MOCK_MONDAY)

        mock_items = [
            {"title": "无效时间", "start_time": "not-a-time", "end_time": "also-bad"},
        ]

        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            result = generate_day_plan(db, now=self.MOCK_WEEKDAY)

        non_work = [e for e in result if not e.get("is_work")]
        assert len(non_work) == 0


# ── target_week_start 辅助函数测试 ────────────────────────────────────────────

class TestTargetWeekStart:
    TZ = ZoneInfo("Asia/Shanghai")

    def test_wednesday_returns_this_monday(self):
        # 2026-06-24 是周三
        wednesday = datetime(2026, 6, 24, 12, 0, tzinfo=self.TZ)
        result = target_week_start(wednesday)
        assert result == datetime(2026, 6, 22)  # 本周一
        assert result.weekday() == 0

    def test_sunday_before_20_returns_this_monday(self):
        # 2026-06-28 是周日
        sunday = datetime(2026, 6, 28, 19, 59, tzinfo=self.TZ)
        result = target_week_start(sunday)
        assert result == datetime(2026, 6, 22)  # 本周一

    def test_sunday_at_20_exact_returns_next_monday(self):
        sunday = datetime(2026, 6, 28, 20, 0, tzinfo=self.TZ)
        result = target_week_start(sunday)
        assert result == datetime(2026, 6, 29)  # 下周一

    def test_sunday_late_night_returns_next_monday(self):
        sunday = datetime(2026, 6, 28, 23, 59, tzinfo=self.TZ)
        result = target_week_start(sunday)
        assert result == datetime(2026, 6, 29)  # 下周一

    def test_monday_midnight_returns_this_monday(self):
        monday = datetime(2026, 6, 22, 0, 0, tzinfo=self.TZ)
        result = target_week_start(monday)
        assert result == datetime(2026, 6, 22)  # 本周一

    def test_returns_naive_datetime(self):
        wednesday = datetime(2026, 6, 24, 12, 0, tzinfo=self.TZ)
        result = target_week_start(wednesday)
        assert result.tzinfo is None


# ── get_or_generate_weekly_plan 测试 ─────────────────────────────────────────

class TestGetOrGenerateWeeklyPlan:
    NOW = datetime(2026, 6, 24, 10, 0)  # 周三，目标周一为 2026-06-22
    TARGET_MONDAY = datetime(2026, 6, 22)

    def test_existing_records_returned_without_llm_call(self, db):
        _add_planned_task(db, "已有任务", week_start=self.TARGET_MONDAY)

        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            tasks, week_start, generated = get_or_generate_weekly_plan(db, now=self.NOW)

        mock_chat.assert_not_called()
        assert generated is False
        assert week_start == self.TARGET_MONDAY
        assert len(tasks) == 1
        assert tasks[0]["title"] == "已有任务"

    def test_no_existing_records_calls_llm_and_persists(self, db):
        _add_memo(db, "买牛奶", memo_type="temporary")
        mock_items = [
            {"title": "买牛奶", "description": "", "duration_hrs": 0.5, "category": "生活",
             "priority": "低", "source_type": "memo", "source_id": 1, "notes": "", "day_of_week": 1},
        ]
        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items) as mock_chat:
            tasks, week_start, generated = get_or_generate_weekly_plan(db, now=self.NOW)

        mock_chat.assert_called_once()
        assert generated is True
        assert week_start == self.TARGET_MONDAY
        assert len(tasks) == 1
        assert db.query(PlannedTask).count() == 1

    def test_existing_records_all_done_still_not_regenerated(self, db):
        _add_planned_task(db, "已完成任务", status="done", week_start=self.TARGET_MONDAY)

        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            tasks, week_start, generated = get_or_generate_weekly_plan(db, now=self.NOW)

        mock_chat.assert_not_called()
        assert generated is False
        assert len(tasks) == 1
        assert tasks[0]["status"] == "done"

    def test_results_sorted_by_day_of_week(self, db):
        t1 = PlannedTask(title="周三任务", week_start_date=self.TARGET_MONDAY, day_of_week=3)
        t2 = PlannedTask(title="周一任务", week_start_date=self.TARGET_MONDAY, day_of_week=1)
        db.add_all([t1, t2])
        db.commit()

        with patch.object(planner_module.llm_client, "chat_json"):
            tasks, _, _ = get_or_generate_weekly_plan(db, now=self.NOW)

        assert [t["title"] for t in tasks] == ["周一任务", "周三任务"]


# ── get_or_generate_day_plan 测试 ────────────────────────────────────────────

class TestGetOrGenerateDayPlan:
    NOW = datetime(2026, 6, 22, 10, 0)  # 周一
    TODAY = datetime(2026, 6, 22)

    def test_existing_records_returned_without_llm_call(self, db):
        e = DayPlan(
            title="已有计划",
            plan_date=self.TODAY,
            start_time=self.TODAY.replace(hour=9),
            end_time=self.TODAY.replace(hour=10),
            duration_minutes=60,
        )
        db.add(e)
        db.commit()

        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            entries, generated = get_or_generate_day_plan(db, now=self.NOW)

        mock_chat.assert_not_called()
        assert generated is False
        assert len(entries) == 1
        assert entries[0]["title"] == "已有计划"

    def test_no_existing_records_calls_llm_and_persists(self, db):
        # 需要有本周待完成的 PlannedTask，generate_day_plan 才会调用 LLM
        _add_planned_task(db, "英语学习", week_start=self.TODAY)
        mock_items = [
            {"title": "英语学习", "start_time": "2026-06-22T15:30:00", "end_time": "2026-06-22T16:30:00",
             "category": "学习", "priority": "中", "planned_task_id": 1, "notes": ""},
        ]
        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items) as mock_chat:
            entries, generated = get_or_generate_day_plan(db, now=self.NOW)

        mock_chat.assert_called_once()
        assert generated is True
        # 工作日会插入工作占位条目
        assert any(e.get("is_work") for e in entries)
        assert db.query(DayPlan).count() == 2  # 工作占位 + LLM 生成的一条

    def test_existing_records_all_done_still_not_regenerated(self, db):
        e = DayPlan(
            title="已完成计划",
            plan_date=self.TODAY,
            start_time=self.TODAY.replace(hour=9),
            end_time=self.TODAY.replace(hour=10),
            duration_minutes=60,
            status="done",
        )
        db.add(e)
        db.commit()

        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            entries, generated = get_or_generate_day_plan(db, now=self.NOW)

        mock_chat.assert_not_called()
        assert generated is False
        assert len(entries) == 1

    def test_results_sorted_by_start_time(self, db):
        e1 = DayPlan(title="下午", plan_date=self.TODAY,
                      start_time=self.TODAY.replace(hour=15), end_time=self.TODAY.replace(hour=16),
                      duration_minutes=60)
        e2 = DayPlan(title="上午", plan_date=self.TODAY,
                      start_time=self.TODAY.replace(hour=9), end_time=self.TODAY.replace(hour=10),
                      duration_minutes=60)
        db.add_all([e1, e2])
        db.commit()

        with patch.object(planner_module.llm_client, "chat_json"):
            entries, _ = get_or_generate_day_plan(db, now=self.NOW)

        assert [e["title"] for e in entries] == ["上午", "下午"]


# ── format_weekly_plan / format_day_plan 排版测试 ─────────────────────────────

class TestFormatWeeklyPlan:
    def test_this_week_header(self):
        # now 是周三 2026-06-24，本周一是 2026-06-22
        now = datetime(2026, 6, 24, 10, 0)
        week_start = datetime(2026, 6, 22)
        tasks = [{"title": "任务A", "day_of_week": 1, "duration_hrs": 1.0, "category": "工作"}]
        text = format_weekly_plan(tasks, week_start, now=now)
        assert "🗓 本周日程" in text
        assert "🗓 下周日程" not in text

    def test_next_week_header(self):
        # now 是周三 2026-06-24，week_start 传下周一 2026-06-29
        now = datetime(2026, 6, 24, 10, 0)
        week_start = datetime(2026, 6, 29)
        tasks = [{"title": "任务A", "day_of_week": 1, "duration_hrs": 1.0, "category": "工作"}]
        text = format_weekly_plan(tasks, week_start, now=now)
        assert "🗓 下周日程" in text

    def test_includes_task_title(self):
        now = datetime(2026, 6, 24, 10, 0)
        week_start = datetime(2026, 6, 22)
        tasks = [{"title": "英语学习", "day_of_week": 2, "duration_hrs": 1.5, "category": "学习"}]
        text = format_weekly_plan(tasks, week_start, now=now)
        assert "英语学习" in text
        assert "1.5 hrs" in text


class TestFormatDayPlan:
    def test_header_present(self):
        assert format_day_plan([]).startswith("🗓 今日计划")

    def test_entry_formatted_with_time_and_title(self):
        entries = [
            {"title": "工作", "start_time": "2026-06-22T07:00:00", "end_time": "2026-06-22T15:00:00",
             "category": "工作"},
        ]
        text = format_day_plan(entries)
        assert "07:00~15:00" in text
        assert "工作" in text
