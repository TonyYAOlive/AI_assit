"""流水记账写入工具：只负责把已经归一化好的字段写入数据库，不做任何解析/校验。"""
from datetime import datetime
from sqlalchemy.orm import Session

from app.models import LedgerEntry


def ledger_entry_create(
        *,
        db: Session,
        amount: float,
        entry_type: str,
        category: str = "其他",
        note: str = "",
        entry_date: datetime,
        raw_input: str = "",
        ) -> dict:
    """创建一条流水记录。调用方需保证 amount/entry_type/category/entry_date 已经过校验与归一化。"""
    entry = LedgerEntry(
        amount=amount,
        entry_type=entry_type,
        category=category,
        note=note,
        entry_date=entry_date,
        raw_input=raw_input,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry.to_dict()
