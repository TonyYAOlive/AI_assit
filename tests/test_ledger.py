"""流水记账模块测试：
- normalize_parsed_entry 对 LLM 抽取结果的归一化/校验逻辑
- stage_pending_entry / pop_pending_entry 待确认状态的内存管理（含 TTL 超时）
- _handle_nlp 记账全流程（暂存确认「是」/「否」/被新消息放弃/超时失效）

数据库和 dependency override 由 conftest.py 统一管理；这里跟 test_telegram_commands.py
一样直接调用 _handle_nlp，不经过 HTTP webhook。

注意：待确认状态（app.services.ledger._pending）是模块级全局字典，各用例通过
autouse fixture 在测试前后清空，避免互相污染；同时不同用例也使用不同的 chat_id
进一步隔离。
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

import app.services.ledger as ledger_module
from app.api.routes import _handle_nlp
from app.models import LedgerEntry
from app.services.ledger import (
    normalize_parsed_entry,
    stage_pending_entry,
    pop_pending_entry,
    has_pending_entry,
)
from tests.conftest import engine

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clear_pending():
    """待确认状态是模块级全局字典，每个用例前后清空，保证测试隔离。"""
    ledger_module._pending.clear()
    yield
    ledger_module._pending.clear()


def _fields(**overrides):
    base = {
        "amount": 30.0,
        "entry_type": "expense",
        "category": "餐饮",
        "note": "午饭",
        "entry_date": datetime(2026, 7, 20),
    }
    base.update(overrides)
    return base


# ── normalize_parsed_entry ───────────────────────────────────────────────────

class TestNormalizeParsedEntry:
    def test_full_valid_data(self):
        now = datetime(2026, 7, 20, 15, 30)
        fields = normalize_parsed_entry({
            "amount": 30,
            "entry_type": "expense",
            "category": "餐饮",
            "note": "午饭",
            "entry_date": "2026-07-20",
        }, now=now)
        assert fields["amount"] == 30.0
        assert fields["entry_type"] == "expense"
        assert fields["category"] == "餐饮"
        assert fields["note"] == "午饭"
        assert fields["entry_date"] == datetime(2026, 7, 20)

    def test_missing_amount_returns_none(self):
        assert normalize_parsed_entry({"entry_type": "expense"}) is None

    def test_non_numeric_amount_returns_none(self):
        assert normalize_parsed_entry({"amount": "abc"}) is None

    def test_zero_amount_returns_none(self):
        assert normalize_parsed_entry({"amount": 0}) is None

    def test_negative_amount_returns_none(self):
        assert normalize_parsed_entry({"amount": -5}) is None

    def test_invalid_entry_type_defaults_to_expense(self):
        fields = normalize_parsed_entry({"amount": 10, "entry_type": "not_a_type"})
        assert fields["entry_type"] == "expense"

    def test_missing_entry_type_defaults_to_expense(self):
        fields = normalize_parsed_entry({"amount": 10})
        assert fields["entry_type"] == "expense"

    def test_category_not_in_expense_list_defaults_to_other(self):
        fields = normalize_parsed_entry({"amount": 10, "entry_type": "expense", "category": "不存在的分类"})
        assert fields["category"] == "其他"

    def test_category_not_in_income_list_defaults_to_other(self):
        fields = normalize_parsed_entry({"amount": 10, "entry_type": "income", "category": "不存在的分类"})
        assert fields["category"] == "其他"

    def test_category_within_list_is_kept_as_is(self):
        fields = normalize_parsed_entry({"amount": 10, "entry_type": "income", "category": "工资"})
        assert fields["category"] == "工资"

    def test_entry_date_missing_defaults_to_now_date(self):
        now = datetime(2026, 7, 20, 15, 30)
        fields = normalize_parsed_entry({"amount": 10}, now=now)
        assert fields["entry_date"] == datetime(2026, 7, 20)

    def test_entry_date_invalid_string_defaults_to_now_date(self):
        now = datetime(2026, 7, 20, 15, 30)
        fields = normalize_parsed_entry({"amount": 10, "entry_date": "not-a-date"}, now=now)
        assert fields["entry_date"] == datetime(2026, 7, 20)

    def test_entry_date_with_time_component_is_zeroed(self):
        fields = normalize_parsed_entry({"amount": 10, "entry_date": "2026-07-18T13:45:00"})
        assert fields["entry_date"] == datetime(2026, 7, 18)


# ── stage_pending_entry / pop_pending_entry ──────────────────────────────────

class TestPendingEntryLifecycle:
    CHAT_ID = 90001

    def test_stage_then_pop_returns_entry_and_clears_it(self):
        stage_pending_entry(self.CHAT_ID, _fields(), raw_input="午饭花了30")
        entry, expired = pop_pending_entry(self.CHAT_ID)
        assert expired is False
        assert entry.amount == 30.0
        assert entry.entry_type == "expense"
        assert entry.category == "餐饮"
        assert entry.note == "午饭"
        assert entry.entry_date == datetime(2026, 7, 20)
        assert entry.raw_input == "午饭花了30"

        # 再次 pop 同一 chat_id 应为空（已被清除）
        entry2, expired2 = pop_pending_entry(self.CHAT_ID)
        assert entry2 is None
        assert expired2 is False

    def test_pop_after_ttl_returns_expired(self):
        stage_pending_entry(self.CHAT_ID, _fields(), raw_input="午饭花了30")
        future = datetime.now() + timedelta(minutes=11)
        entry, expired = pop_pending_entry(self.CHAT_ID, now=future)
        assert entry is None
        assert expired is True
        # 超时项已被清理
        assert has_pending_entry(self.CHAT_ID) is False

    def test_consecutive_stage_overwrites_previous(self):
        stage_pending_entry(self.CHAT_ID, _fields(amount=10.0), raw_input="第一笔")
        stage_pending_entry(self.CHAT_ID, _fields(amount=99.0), raw_input="第二笔")
        entry, expired = pop_pending_entry(self.CHAT_ID)
        assert expired is False
        assert entry.amount == 99.0
        assert entry.raw_input == "第二笔"

    def test_pop_never_staged_returns_none_not_expired(self):
        entry, expired = pop_pending_entry(self.CHAT_ID)
        assert entry is None
        assert expired is False


# ── _handle_nlp 记账全流程 ─────────────────────────────────────────────────────

def _ledger_route_result(**data_overrides):
    data = {
        "amount": 30,
        "entry_type": "expense",
        "category": "餐饮",
        "note": "午饭",
        "entry_date": "2026-07-20",
    }
    data.update(data_overrides)
    return {"intent": "add_ledger_entry", "data": data}


class TestHandleNlpLedgerFlow:
    def test_valid_ledger_intent_stages_without_persisting(self, db):
        chat_id = 90101
        with patch("app.api.routes.llm_route", return_value=_ledger_route_result()):
            reply = _handle_nlp("午饭花了30", db, chat_id)
        assert "请确认这笔支出" in reply
        assert "回复「是」确认入账，回复「否」放弃。" in reply
        assert db.query(LedgerEntry).count() == 0
        assert has_pending_entry(chat_id) is True

    def test_missing_amount_returns_error_without_staging(self, db):
        chat_id = 90102
        with patch("app.api.routes.llm_route", return_value=_ledger_route_result(amount=None)):
            reply = _handle_nlp("随便花了点钱", db, chat_id)
        assert reply == "没有识别到有效金额，请重新描述这笔收支，例如：午饭花了30"
        assert db.query(LedgerEntry).count() == 0
        assert has_pending_entry(chat_id) is False

    def test_confirm_yes_persists_and_does_not_call_llm_route(self, db):
        chat_id = 90103
        stage_pending_entry(chat_id, _fields(), raw_input="午饭花了30")
        with patch("app.api.routes.llm_route") as mock_route:
            reply = _handle_nlp("是", db, chat_id)
        mock_route.assert_not_called()
        assert "已记录" in reply
        entries = db.query(LedgerEntry).all()
        assert len(entries) == 1
        assert entries[0].amount == 30.0
        assert entries[0].category == "餐饮"
        assert has_pending_entry(chat_id) is False

    def test_confirm_no_cancels_without_persisting(self, db):
        chat_id = 90104
        stage_pending_entry(chat_id, _fields(), raw_input="午饭花了30")
        with patch("app.api.routes.llm_route") as mock_route:
            reply = _handle_nlp("否", db, chat_id)
        mock_route.assert_not_called()
        assert reply == "好的，已取消这笔记录。"
        assert db.query(LedgerEntry).count() == 0
        assert has_pending_entry(chat_id) is False

    def test_unrelated_message_abandons_pending_and_processes_normally(self, db):
        chat_id = 90105
        stage_pending_entry(chat_id, _fields(), raw_input="午饭花了30")
        with patch("app.api.routes.llm_route", return_value={"intent": "greet", "data": {}}):
            reply = _handle_nlp("你好呀", db, chat_id)
        assert reply.startswith("（已放弃上一条未确认的流水）\n")
        assert "你好" in reply
        assert db.query(LedgerEntry).count() == 0
        assert has_pending_entry(chat_id) is False

    def test_new_ledger_request_abandons_old_pending_and_stages_new(self, db):
        chat_id = 90106
        stage_pending_entry(chat_id, _fields(amount=10.0, note="旧的"), raw_input="旧的记账消息")
        with patch("app.api.routes.llm_route", return_value=_ledger_route_result(amount=88, note="新的")):
            reply = _handle_nlp("新的记账消息", db, chat_id)
        assert reply.startswith("（已放弃上一条未确认的流水）\n")
        assert "请确认这笔支出" in reply
        assert db.query(LedgerEntry).count() == 0

        entry, expired = pop_pending_entry(chat_id)
        assert expired is False
        assert entry.amount == 88.0
        assert entry.note == "新的"

    def test_expired_pending_shows_timeout_notice_and_processes_current_message(self, db):
        chat_id = 90107
        stage_pending_entry(chat_id, _fields(), raw_input="午饭花了30")
        # 直接操纵模块内部状态，让暂存项已超过 TTL
        ledger_module._pending[chat_id].staged_at = datetime.now() - timedelta(minutes=11)
        with patch("app.api.routes.llm_route", return_value={"intent": "greet", "data": {}}):
            reply = _handle_nlp("你好", db, chat_id)
        assert reply.startswith("（上一条待确认的记账请求已超时失效，如需记账请重新描述）\n")
        assert "你好" in reply
        assert db.query(LedgerEntry).count() == 0
        assert has_pending_entry(chat_id) is False
