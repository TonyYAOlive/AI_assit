"""计划生成器测试：mock LLM，验证 PlannedTask 和 DayPlan 记录生成。"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.planner as planner_module
from app.models import Base, Memo, PlannedTask, DayPlan
from app.services.planner import generate_weekly_tasks, generate_day_plan, _week_start

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


# ── _week_start 辅助函数测试 ──────────────────────────────────────────────────

class TestWeekStart:
    def test_returns_next_monday(self):
        tuesday = datetime(2026, 6, 23)  # 周二
        result = _week_start(tuesday)
        assert result == datetime(2026, 6, 29)  # 下周一
        assert result.weekday() == 0

    def test_on_monday_returns_next_monday(self):
        monday = datetime(2026, 6, 22)  # 周一
        result = _week_start(monday)
        assert result == datetime(2026, 6, 29)  # 下周一（不是本周一）
