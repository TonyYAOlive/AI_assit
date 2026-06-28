from datetime import datetime
from sqlalchemy.orm import Session

from app.models import Memo



def memo_create(
        *,
        db: Session,
        content: str,
        memo_type: str = "temporary",
        priority: str = "low",
        status: str = "pending",
        due_time: datetime | None = None,
        planned_time: datetime | None = None,
        estimated_minutes: int = 30,
        raw_input: str = "",
        ) -> dict:

    """创建备忘录"""
    memo = Memo(
        content=content,
        memo_type=memo_type,
        priority=priority,
        status=status,
        due_time=due_time,
        planned_time=planned_time,
        estimated_minutes=estimated_minutes,
        raw_input=raw_input,
    )
    db.add(memo)
    db.commit()
    db.refresh(memo)

    return memo.to_dict()