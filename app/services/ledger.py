"""流水记账业务逻辑：LLM 抽取结果的归一化、"待确认"状态的内存管理、回复文案格式化。"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import TIMEZONE

EXPENSE_CATEGORIES = ["餐饮", "交通", "购物", "娱乐", "居住", "医疗", "教育", "人情", "其他"]
INCOME_CATEGORIES = ["工资", "奖金", "报销", "理财", "兼职", "其他"]

_VALID_ENTRY_TYPES = {"income", "expense"}
_DEFAULT_ENTRY_TYPE = "expense"
_DEFAULT_CATEGORY = "其他"
_PENDING_TTL = timedelta(minutes=10)


def _categories_for(entry_type: str) -> list[str]:
    return INCOME_CATEGORIES if entry_type == "income" else EXPENSE_CATEGORIES


def normalize_parsed_entry(data: dict, now: datetime = None) -> dict | None:
    """把 router.py 中 add_ledger_entry 意图抽取出的 data 归一化成落库用字段。
    校验失败（金额缺失/非正数）返回 None。
    返回字段：amount(float) / entry_type(str) / category(str) / note(str) / entry_date(datetime，当天零点)
    """
    if now is None:
        now = datetime.now(ZoneInfo(TIMEZONE))

    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None

    entry_type = data.get("entry_type")
    if entry_type not in _VALID_ENTRY_TYPES:
        entry_type = _DEFAULT_ENTRY_TYPE

    category = data.get("category")
    if category not in _categories_for(entry_type):
        category = _DEFAULT_CATEGORY

    note = (data.get("note") or "").strip()

    entry_date_str = data.get("entry_date")
    entry_date = None
    if entry_date_str:
        try:
            entry_date = datetime.fromisoformat(entry_date_str)
        except ValueError:
            entry_date = None
    if entry_date is None:
        entry_date = datetime(now.year, now.month, now.day)
    else:
        entry_date = datetime(entry_date.year, entry_date.month, entry_date.day)

    return {
        "amount": round(amount, 2),
        "entry_type": entry_type,
        "category": category,
        "note": note,
        "entry_date": entry_date,
    }


@dataclass
class PendingLedgerEntry:
    amount: float
    entry_type: str
    category: str
    note: str
    entry_date: datetime
    raw_input: str
    staged_at: datetime = field(default_factory=datetime.now)


_pending: dict[int, PendingLedgerEntry] = {}
_lock = threading.Lock()


def stage_pending_entry(chat_id: int, fields: dict, raw_input: str) -> PendingLedgerEntry:
    """暂存一条待确认流水，若该 chat_id 已有待确认项，直接覆盖（丢弃旧的）。"""
    entry = PendingLedgerEntry(raw_input=raw_input, **fields)
    with _lock:
        _pending[chat_id] = entry
    return entry


def pop_pending_entry(chat_id: int, now: datetime = None) -> tuple:
    """取出并清除该 chat_id 的待确认项。
    返回 (entry, expired)：
      - 有效待确认项存在 -> (entry, False)
      - 从未有待确认项 -> (None, False)
      - 曾经有但已超过 _PENDING_TTL -> (None, True)（同时已清理内存）
    """
    if now is None:
        now = datetime.now()
    with _lock:
        entry = _pending.pop(chat_id, None)
    if entry is None:
        return None, False
    if now - entry.staged_at > _PENDING_TTL:
        return None, True
    return entry, False


def has_pending_entry(chat_id: int) -> bool:
    with _lock:
        return chat_id in _pending


_TYPE_LABEL = {"income": "收入", "expense": "支出"}


def format_confirmation_prompt(fields: dict) -> str:
    type_label = _TYPE_LABEL.get(fields["entry_type"], fields["entry_type"])
    date_str = fields["entry_date"].strftime("%Y-%m-%d")
    note = fields["note"] or "（无）"
    return (
        f"请确认这笔{type_label}：\n"
        f"金额：{fields['amount']} 元\n"
        f"分类：{fields['category']}\n"
        f"备注：{note}\n"
        f"日期：{date_str}\n\n"
        f"回复「是」确认入账，回复「否」放弃。"
    )


def format_ledger_entry(entry_dict: dict) -> str:
    type_label = _TYPE_LABEL.get(entry_dict["entry_type"], entry_dict["entry_type"])
    date_str = entry_dict["entry_date"][:10] if entry_dict.get("entry_date") else ""
    return f"{date_str} {type_label} {entry_dict['category']} {entry_dict['amount']}元（{entry_dict['note'] or '无备注'}）"
