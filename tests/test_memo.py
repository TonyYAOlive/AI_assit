"""Memo CRUD 路由测试。使用内存 SQLite，不依赖 LLM。
数据库和 dependency override 由 conftest.py 统一管理。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def create_memo(**kwargs):
    payload = {"content": "测试备忘", **kwargs}
    return client.post("/api/memos", json=payload)


# ── 创建 ──────────────────────────────────────────────────────────────────────

class TestCreateMemo:
    def test_create_minimal(self):
        r = create_memo(content="买牛奶")
        assert r.status_code == 200
        data = r.json()
        assert data["content"] == "买牛奶"
        assert data["memo_type"] == "temporary"
        assert data["priority"] == "low"
        assert data["status"] == "pending"
        assert data["id"] is not None

    def test_create_full_fields(self):
        r = create_memo(
            content="每周英语学习",
            memo_type="long_term",
            priority="high",
            estimated_minutes=60,
            due_time="2026-12-31T23:59:00",
            planned_time="2026-07-01T09:00:00",
        )
        assert r.status_code == 200
        data = r.json()
        assert data["memo_type"] == "long_term"
        assert data["priority"] == "high"
        assert data["estimated_minutes"] == 60
        assert data["due_time"] is not None
        assert data["planned_time"] is not None

    def test_create_empty_content_returns_400(self):
        r = create_memo(content="   ")
        assert r.status_code == 400

    def test_create_invalid_memo_type_returns_400(self):
        r = create_memo(content="测试", memo_type="invalid_type")
        assert r.status_code == 400

    def test_create_invalid_priority_returns_400(self):
        r = create_memo(content="测试", priority="critical")
        assert r.status_code == 400

    def test_priority_is_persisted(self):
        r = create_memo(content="重要任务", priority="urgent")
        assert r.status_code == 200
        assert r.json()["priority"] == "urgent"


# ── 列表查询 ──────────────────────────────────────────────────────────────────

class TestListMemos:
    def test_list_empty(self):
        r = client.get("/api/memos")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_all(self):
        create_memo(content="备忘1")
        create_memo(content="备忘2")
        r = client.get("/api/memos")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_filter_by_status(self):
        create_memo(content="待办")
        r1 = create_memo(content="已完成")
        client.patch(f"/api/memos/{r1.json()['id']}/status", json={"status": "done"})

        r = client.get("/api/memos?status=pending")
        assert r.status_code == 200
        assert all(m["status"] == "pending" for m in r.json())
        assert len(r.json()) == 1

    def test_filter_by_memo_type(self):
        create_memo(content="临时", memo_type="temporary")
        create_memo(content="长期", memo_type="long_term")

        r = client.get("/api/memos?memo_type=long_term")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["memo_type"] == "long_term"

    def test_filter_by_priority(self):
        create_memo(content="低优先级", priority="low")
        create_memo(content="高优先级", priority="high")

        r = client.get("/api/memos?priority=high")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["priority"] == "high"

    def test_list_ordered_by_created_at_desc(self):
        create_memo(content="先创建的")
        create_memo(content="后创建的")
        r = client.get("/api/memos")
        items = r.json()
        assert items[0]["content"] == "后创建的"
        assert items[1]["content"] == "先创建的"


# ── 获取单条 ──────────────────────────────────────────────────────────────────

class TestGetMemo:
    def test_get_existing(self):
        created = create_memo(content="单条备忘").json()
        r = client.get(f"/api/memos/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]
        assert r.json()["content"] == "单条备忘"

    def test_get_nonexistent_returns_404(self):
        r = client.get("/api/memos/99999")
        assert r.status_code == 404


# ── 状态更新 ──────────────────────────────────────────────────────────────────

class TestUpdateMemoStatus:
    def test_update_to_done(self):
        memo_id = create_memo(content="待完成").json()["id"]
        r = client.patch(f"/api/memos/{memo_id}/status", json={"status": "done"})
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_update_to_planned(self):
        memo_id = create_memo(content="待计划").json()["id"]
        r = client.patch(f"/api/memos/{memo_id}/status", json={"status": "planned"})
        assert r.status_code == 200
        assert r.json()["status"] == "planned"

    def test_update_to_cancelled(self):
        memo_id = create_memo(content="取消").json()["id"]
        r = client.patch(f"/api/memos/{memo_id}/status", json={"status": "cancelled"})
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"

    def test_update_to_expired(self):
        memo_id = create_memo(content="过期").json()["id"]
        r = client.patch(f"/api/memos/{memo_id}/status", json={"status": "expired"})
        assert r.status_code == 200
        assert r.json()["status"] == "expired"

    def test_invalid_status_returns_400(self):
        memo_id = create_memo(content="无效状态").json()["id"]
        r = client.patch(f"/api/memos/{memo_id}/status", json={"status": "flying"})
        assert r.status_code == 400

    def test_nonexistent_returns_404(self):
        r = client.patch("/api/memos/99999/status", json={"status": "done"})
        assert r.status_code == 404


# ── 删除 ─────────────────────────────────────────────────────────────────────

class TestDeleteMemo:
    def test_delete_existing(self):
        memo_id = create_memo(content="待删除").json()["id"]
        r = client.delete(f"/api/memos/{memo_id}")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

        r2 = client.get(f"/api/memos/{memo_id}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self):
        r = client.delete("/api/memos/99999")
        assert r.status_code == 404
