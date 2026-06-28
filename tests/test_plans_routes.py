"""PlannedTask 和 DayPlan CRUD 路由测试。不依赖 LLM。
数据库和 dependency override 由 conftest.py 统一管理。
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.services.planner as planner_module
from app.models import PlannedTask, DayPlan
from app.main import app
from tests.conftest import engine

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
client = TestClient(app)


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _next_monday():
    now = datetime.now()
    days_ahead = (7 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _create_planned_task(title="测试任务", duration_hrs=1.0, week_start=None):
    db = TestingSessionLocal()
    ws = week_start or datetime.now()
    monday = ws - timedelta(days=ws.weekday())
    monday = datetime(monday.year, monday.month, monday.day)
    t = PlannedTask(title=title, week_start_date=monday, duration_hrs=duration_hrs)
    db.add(t)
    db.commit()
    db.refresh(t)
    task_id = t.id
    db.close()
    return task_id


def _create_day_plan(title="测试计划"):
    db = TestingSessionLocal()
    today = datetime.now()
    today_midnight = datetime(today.year, today.month, today.day)
    e = DayPlan(
        title=title,
        plan_date=today_midnight,
        start_time=today_midnight.replace(hour=15),
        end_time=today_midnight.replace(hour=16),
        duration_minutes=60,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    entry_id = e.id
    db.close()
    return entry_id


# ── PlannedTask CRUD ──────────────────────────────────────────────────────────

class TestListPlans:
    def test_list_empty(self):
        r = client.get("/api/plans")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_items(self):
        _create_planned_task("任务A")
        _create_planned_task("任务B")
        r = client.get("/api/plans")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_filter_by_status(self):
        task_id = _create_planned_task("待完成")
        db = TestingSessionLocal()
        t = db.get(PlannedTask, task_id)
        t.status = "done"
        db.commit()
        db.close()

        _create_planned_task("未完成")

        r = client.get("/api/plans?status=pending")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["title"] == "未完成"

    def test_filter_invalid_date_returns_400(self):
        r = client.get("/api/plans?week_start=not-a-date")
        assert r.status_code == 400


class TestGetPlan:
    def test_get_existing(self):
        task_id = _create_planned_task("单条任务")
        r = client.get(f"/api/plans/{task_id}")
        assert r.status_code == 200
        assert r.json()["title"] == "单条任务"

    def test_get_nonexistent_returns_404(self):
        r = client.get("/api/plans/99999")
        assert r.status_code == 404


class TestUpdatePlanStatus:
    def test_update_to_done(self):
        task_id = _create_planned_task()
        r = client.patch(f"/api/plans/{task_id}/status", json={"status": "done"})
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_update_to_cancelled(self):
        task_id = _create_planned_task()
        r = client.patch(f"/api/plans/{task_id}/status", json={"status": "cancelled"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_invalid_status_returns_400(self):
        task_id = _create_planned_task()
        r = client.patch(f"/api/plans/{task_id}/status", json={"status": "flying"})
        assert r.status_code == 400

    def test_nonexistent_returns_404(self):
        r = client.patch("/api/plans/99999/status", json={"status": "done"})
        assert r.status_code == 404


class TestDeletePlan:
    def test_delete_existing(self):
        task_id = _create_planned_task()
        r = client.delete(f"/api/plans/{task_id}")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert client.get(f"/api/plans/{task_id}").status_code == 404

    def test_delete_nonexistent_returns_404(self):
        r = client.delete("/api/plans/99999")
        assert r.status_code == 404


class TestGeneratePlans:
    def test_generate_returns_tasks(self):
        mock_items = [
            {"title": "英语学习", "description": "", "duration_hrs": 1.0,
             "category": "学习", "priority": "中", "source_type": "manual",
             "source_id": None, "notes": ""},
        ]
        with patch.object(planner_module.llm_client, "chat_json", return_value=mock_items):
            r = client.post("/api/plans/generate")
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert r.json()["tasks"][0]["title"] == "英语学习"

    def test_generate_empty_returns_zero(self):
        with patch.object(planner_module.llm_client, "chat_json", return_value=[]):
            r = client.post("/api/plans/generate")
        assert r.status_code == 200
        assert r.json()["count"] == 0


# ── DayPlan CRUD ──────────────────────────────────────────────────────────────

class TestListDayPlans:
    def test_list_empty(self):
        r = client.get("/api/day_plans")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_items(self):
        _create_day_plan("计划A")
        _create_day_plan("计划B")
        r = client.get("/api/day_plans")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_filter_by_date(self):
        _create_day_plan("今天的计划")
        today = _today_str()
        r = client.get(f"/api/day_plans?date={today}")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_filter_invalid_date_returns_400(self):
        r = client.get("/api/day_plans?date=not-a-date")
        assert r.status_code == 400


class TestGetDayPlan:
    def test_get_existing(self):
        entry_id = _create_day_plan("单条计划")
        r = client.get(f"/api/day_plans/{entry_id}")
        assert r.status_code == 200
        assert r.json()["title"] == "单条计划"

    def test_get_nonexistent_returns_404(self):
        r = client.get("/api/day_plans/99999")
        assert r.status_code == 404


class TestUpdateDayPlanStatus:
    def test_update_to_done(self):
        entry_id = _create_day_plan()
        r = client.patch(f"/api/day_plans/{entry_id}/status", json={"status": "done"})
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_invalid_status_returns_400(self):
        entry_id = _create_day_plan()
        r = client.patch(f"/api/day_plans/{entry_id}/status", json={"status": "flying"})
        assert r.status_code == 400

    def test_nonexistent_returns_404(self):
        r = client.patch("/api/day_plans/99999/status", json={"status": "done"})
        assert r.status_code == 404


class TestDeleteDayPlan:
    def test_delete_existing(self):
        entry_id = _create_day_plan()
        r = client.delete(f"/api/day_plans/{entry_id}")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert client.get(f"/api/day_plans/{entry_id}").status_code == 404

    def test_delete_nonexistent_returns_404(self):
        r = client.delete("/api/day_plans/99999")
        assert r.status_code == 404


class TestGenerateDayPlan:
    def test_generate_returns_entries(self):
        # 通过 patch generate_day_plan 函数，注入固定的工作日 now
        mock_weekday = datetime(2026, 6, 22, 10, 0)
        real_generate = planner_module.generate_day_plan

        def patched_generate(db, now=None):
            return real_generate(db, now=mock_weekday)

        with patch("app.api.routes.generate_day_plan", side_effect=patched_generate):
            r = client.post("/api/day_plans/generate")

        assert r.status_code == 200
        entries = r.json()["entries"]
        work = [e for e in entries if e["is_work"]]
        assert len(work) == 1
