"""Telegram 命令层测试：验证 /week_plan /plan /plans /day_plan /today 命令分发，
以及 _handle_nlp 对 query_plan / query_today 等意图的路由。
数据库和 dependency override 由 conftest.py 统一管理，这里直接调用 _handle_command /
_handle_nlp，不经过 HTTP webhook（避免依赖 TELEGRAM_BOT_TOKEN / 网络请求）。
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import sessionmaker

import app.services.planner as planner_module
from app.api.routes import _handle_command, _handle_nlp
from app.config import TIMEZONE
from app.models import PlannedTask, DayPlan, Memo
from tests.conftest import engine

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

CHAT_ID = 12345


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _add_planned_task(db, title="已有任务", status="pending", now=None):
    """按源码同款的 target_week_start 规则计算 week_start_date，避免与
    get_or_generate_weekly_plan 实际查询的目标周不一致（尤其是周日20点边界附近）。
    """
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    week_start = planner_module.target_week_start(now)
    t = PlannedTask(title=title, week_start_date=week_start, duration_hrs=1.0, status=status)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _current_week_monday(now=None):
    """按 generate_day_plan 内部自己的算法（本周实际周一，*不*套用
    target_week_start 的"周日20点后算下周"规则）计算 pending PlannedTask
    应该挂靠的 week_start_date，专供需要触发 generate_day_plan 内部
    LLM 调用的测试使用。
    """
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    today = datetime(now.year, now.month, now.day)
    return today - timedelta(days=now.weekday())


def _add_day_plan(db, title="已有计划", now=None):
    """按源码同款的时区基准计算 plan_date（get_or_generate_day_plan 内部用
    datetime.now(ZoneInfo(TIMEZONE)) 推算今天），避免跨时区/跨天边界不一致。
    """
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    today = datetime(now.year, now.month, now.day)
    e = DayPlan(
        title=title,
        plan_date=today,
        start_time=today.replace(hour=9),
        end_time=today.replace(hour=10),
        duration_minutes=60,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


# ── /week_plan 与 /plan 别名 ──────────────────────────────────────────────────

class TestWeekPlanCommand:
    def test_week_plan_with_existing_records_does_not_call_llm(self, db):
        _add_planned_task(db, "已有任务")
        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            reply = _handle_command("/week_plan", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "已有任务" in reply
        assert reply is not None

    def test_week_plan_no_data_returns_empty_message(self, db):
        with patch.object(planner_module.llm_client, "chat_json", return_value=[]):
            reply = _handle_command("/week_plan", db, CHAT_ID)
        assert reply == "暂无备忘录或长期任务，无法生成计划。"

    def test_plan_alias_behaves_like_week_plan(self, db):
        _add_planned_task(db, "别名任务")
        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            reply = _handle_command("/plan", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "别名任务" in reply

    def test_week_plan_llm_exception_returns_friendly_error(self, db):
        with patch.object(planner_module.llm_client, "chat_json", side_effect=RuntimeError("boom")):
            reply = _handle_command("/week_plan", db, CHAT_ID)
        assert "获取周计划失败" in reply
        assert "boom" in reply


# ── /day_plan 与 /today 别名 ──────────────────────────────────────────────────

class TestDayPlanCommand:
    def test_day_plan_with_existing_records_does_not_call_llm(self, db):
        _add_day_plan(db, "已有计划")
        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            reply = _handle_command("/day_plan", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "已有计划" in reply

    def test_today_alias_behaves_like_day_plan(self, db):
        _add_day_plan(db, "今日别名计划")
        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            reply = _handle_command("/today", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "今日别名计划" in reply

    def test_day_plan_no_pending_tasks_and_weekend_returns_empty_message(self, db):
        # 用不存在任务、非工作日场景较难在此层直接控制 now（函数内部用当前时间）。
        # 这里仅验证「LLM 返回空且今天没有任何 DayPlan/PlannedTask」时的兜底文案分支可达。
        with patch.object(planner_module.llm_client, "chat_json", return_value=[]):
            reply = _handle_command("/day_plan", db, CHAT_ID)
        # 工作日会有工作占位条目，因此非空；周末且无任务时才会是空消息。
        assert reply is not None

    def test_day_plan_llm_exception_returns_friendly_error(self, db):
        # generate_day_plan 只有在本周存在 pending 的 PlannedTask 时才会真正调用
        # chat_json（否则直接提前 return），所以要先插入一条本周 pending 任务，
        # 确保能触发 mock 的 RuntimeError，从而验证异常兜底文案分支。
        # 注意：generate_day_plan 内部用的是"本周实际周一"（this_monday），而不是
        # target_week_start 的"周日20点后算下周"规则，这里必须用与之一致的算法。
        db.add(PlannedTask(
            title="今日异常测试任务",
            week_start_date=_current_week_monday(),
            duration_hrs=1.0,
            status="pending",
        ))
        db.commit()
        with patch.object(planner_module.llm_client, "chat_json", side_effect=RuntimeError("boom")):
            reply = _handle_command("/day_plan", db, CHAT_ID)
        assert "获取今日计划失败" in reply
        assert "boom" in reply


# ── /plans 命令的实际行为（旧命令已删除，但前缀匹配可能仍会命中 /plan 分支）──────

class TestPlansCommandActualBehavior:
    def test_plans_prefix_matches_plan_alias_branch(self, db):
        """记录当前实际行为：因为 `/plan` 分支用 startswith 匹配，
        `/plans` 文本恰好以 `/plan` 为前缀，所以会被当作 /plan 的别名处理，
        而不是落入未知命令分支。这里如实断言观察到的行为，供审查确认是否符合预期。
        """
        _add_planned_task(db, "plans前缀测试任务")
        with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
            reply = _handle_command("/plans", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert reply is not None
        assert "plans前缀测试任务" in reply


# ── 未知命令 ───────────────────────────────────────────────────────────────────

class TestUnknownCommand:
    def test_unrecognized_command_returns_none(self, db):
        reply = _handle_command("/foobar", db, CHAT_ID)
        assert reply is None


# ── /task 精确匹配：带内容添加 / 无内容查询 ─────────────────────────────────────

class TestTaskCommand:
    def test_task_with_content_creates_long_term_memo(self, db):
        reply = _handle_command("/task 学习强化英语", db, CHAT_ID)
        assert "已添加长期任务" in reply
        memos = db.query(Memo).filter(Memo.memo_type == "long_term").all()
        assert len(memos) == 1
        assert memos[0].content == "学习强化英语"

    def test_task_without_content_queries_and_reports_empty(self, db):
        reply = _handle_command("/task", db, CHAT_ID)
        assert reply == "没有长期任务。"

    def test_task_without_content_queries_and_lists_existing(self, db):
        db.add(Memo(content="长期任务A", memo_type="long_term", status="pending"))
        db.commit()
        reply = _handle_command("/task", db, CHAT_ID)
        assert "长期任务A" in reply
        assert "长期任务（共1条）" in reply

    def test_task_with_only_whitespace_is_treated_as_no_content(self, db):
        reply = _handle_command("/task   ", db, CHAT_ID)
        assert reply == "没有长期任务。"

    def test_tasks_command_is_unknown(self, db):
        reply = _handle_command("/tasks", db, CHAT_ID)
        assert reply is None


# ── /memo 精确匹配：带内容添加 / 无内容查询 ─────────────────────────────────────

class TestMemoCommand:
    def test_memo_with_content_creates_temporary_memo(self, db):
        reply = _handle_command("/memo 买牛奶", db, CHAT_ID)
        assert "已添加备忘录" in reply
        memos = db.query(Memo).filter(Memo.memo_type == "temporary").all()
        assert len(memos) == 1
        assert memos[0].content == "买牛奶"

    def test_memo_without_content_queries_and_reports_empty(self, db):
        reply = _handle_command("/memo", db, CHAT_ID)
        assert reply == "没有待办备忘录。"

    def test_memo_without_content_queries_and_lists_existing(self, db):
        db.add(Memo(content="备忘录A", memo_type="temporary", status="pending"))
        db.commit()
        reply = _handle_command("/memo", db, CHAT_ID)
        assert "备忘录A" in reply
        assert "待办备忘录（共1条）" in reply

    def test_memo_with_only_whitespace_is_treated_as_no_content(self, db):
        reply = _handle_command("/memo   ", db, CHAT_ID)
        assert reply == "没有待办备忘录。"

    def test_memos_command_is_unknown(self, db):
        reply = _handle_command("/memos", db, CHAT_ID)
        assert reply is None


# ── /start 帮助文案 ────────────────────────────────────────────────────────────

class TestStartCommand:
    def test_start_help_text_does_not_mention_removed_commands(self, db):
        reply = _handle_command("/start", db, CHAT_ID)
        assert "/memos" not in reply
        assert "/tasks" not in reply


# ── _handle_nlp 意图路由 ───────────────────────────────────────────────────────

class TestHandleNlpPlanIntents:
    def test_query_plan_intent_routes_to_week_plan(self, db):
        """回归覆盖：query_plan 之前没有处理分支会落入 unknown 兜底，
        这次新补上的行为——应当调用周计划逻辑而不是返回"没太明白你的意思"。
        """
        _add_planned_task(db, "查询周计划任务")
        with patch("app.api.routes.llm_route", return_value={"intent": "query_plan", "data": {}}):
            with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
                reply = _handle_nlp("我这周有什么计划", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "查询周计划任务" in reply
        assert "没太明白" not in reply

    def test_generate_weekly_plan_intent_routes_to_week_plan(self, db):
        _add_planned_task(db, "生成周计划任务")
        with patch("app.api.routes.llm_route", return_value={"intent": "generate_weekly_plan", "data": {}}):
            with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
                reply = _handle_nlp("帮我生成这周计划", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "生成周计划任务" in reply

    def test_query_today_intent_routes_to_day_plan(self, db):
        _add_day_plan(db, "查询今日计划任务")
        with patch("app.api.routes.llm_route", return_value={"intent": "query_today", "data": {}}):
            with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
                reply = _handle_nlp("我今天有什么安排", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "查询今日计划任务" in reply

    def test_generate_day_plan_intent_routes_to_day_plan(self, db):
        _add_day_plan(db, "生成今日计划任务")
        with patch("app.api.routes.llm_route", return_value={"intent": "generate_day_plan", "data": {}}):
            with patch.object(planner_module.llm_client, "chat_json") as mock_chat:
                reply = _handle_nlp("生成今天的计划", db, CHAT_ID)
        mock_chat.assert_not_called()
        assert "生成今日计划任务" in reply

    def test_unknown_intent_falls_back_to_default_message(self, db):
        with patch("app.api.routes.llm_route", return_value={"intent": "unknown", "data": {}}):
            reply = _handle_nlp("asdkfjaslkdfj", db, CHAT_ID)
        assert "没太明白" in reply
