"""数据库模型定义。"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, Float, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

# SQLite 需要 check_same_thread=False 才能在 FastAPI 的多线程下使用
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    description = Column(Text, default="")
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, default=60)
    location = Column(String(200), default="")
    participants = Column(JSON, default=list)        # ["老王", "小李"]
    category = Column(String(20), default="其他")    # 会议/工作/生活/学习/运动/其他
    priority = Column(String(10), default="中")      # 高/中/低
    status = Column(String(20), default="pending", index=True)  # pending/done/cancelled
    reminder_minutes_before = Column(Integer, default=15)
    raw_input = Column(Text, default="")             # 保留原始用户输入
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_minutes": self.duration_minutes,
            "location": self.location,
            "participants": self.participants,
            "category": self.category,
            "priority": self.priority,
            "status": self.status,
            "reminder_minutes_before": self.reminder_minutes_before,
            "raw_input": self.raw_input,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }



class Memo(Base):
    __tablename__ = "memos"

    id = Column(Integer, primary_key=True, index=True)

    # 备忘内容，例如：清理车、洗衣服、每周英语学习5小时
    content = Column(Text, nullable=False)

    # temporary = 临时备忘，只做一次
    # long_term = 长期备忘，反复参与计划
    memo_type = Column(String, default="temporary", index=True)

    # low / normal / high / urgent
    priority = Column(String, default="low", index=True)

    # pending / planned / done / cancelled / expired
    status = Column(String, default="pending", index=True)

    # 截止时间，可为空。long_term 默认不需要 due_time
    due_time = Column(DateTime, nullable=True, index=True)

    # 计划什么时候做，可为空
    planned_time = Column(DateTime, nullable=True, index=True)

    # 预计耗时，单位：分钟
    estimated_minutes = Column(Integer, default=30)

    # 用户原始输入
    raw_input = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "memo_type": self.memo_type,
            "priority": self.priority,
            "status": self.status,
            "due_time": self.due_time.isoformat() if self.due_time else None,
            "planned_time": self.planned_time.isoformat() if self.planned_time else None,
            "estimated_minutes": self.estimated_minutes,
            "raw_input": self.raw_input,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class PlannedTask(Base):
    """周计划任务表：无具体时间，只有预计时长，由 LLM 根据备忘录和长期任务生成。"""
    __tablename__ = "planned_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    description = Column(Text, default="")
    week_start_date = Column(DateTime, nullable=False, index=True)  # 所属周的周一
    duration_hrs = Column(Float, default=1.0)                       # 预计时长（小时）
    category = Column(String(20), default="其他")
    priority = Column(String(10), default="中")                     # 高/中/低
    status = Column(String(20), default="pending", index=True)      # pending/done/cancelled
    source_type = Column(String(20), default="")                    # memo/task/manual
    source_id = Column(Integer, nullable=True)                      # 来源 Memo ID
    day_of_week = Column(Integer, default=1)                        # 1=周一 … 7=周日
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "week_start_date": self.week_start_date.isoformat() if self.week_start_date else None,
            "duration_hrs": self.duration_hrs,
            "category": self.category,
            "priority": self.priority,
            "status": self.status,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "day_of_week": self.day_of_week,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DayPlan(Base):
    """当日计划表：含具体 start_time/end_time，由 /day_plan 触发生成。"""
    __tablename__ = "day_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)
    plan_date = Column(DateTime, nullable=False, index=True)        # 所属日期（当天零点）
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, default=60)                  # 由 start/end 推算
    category = Column(String(20), default="其他")
    priority = Column(String(10), default="中")
    status = Column(String(20), default="pending", index=True)      # pending/done/cancelled
    planned_task_id = Column(Integer, nullable=True)                # 来源 PlannedTask ID（工作块为 null）
    is_work = Column(Boolean, default=False)                        # True = 工作条目，便于将来 real 日程替换
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "plan_date": self.plan_date.isoformat() if self.plan_date else None,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_minutes": self.duration_minutes,
            "category": self.category,
            "priority": self.priority,
            "status": self.status,
            "planned_task_id": self.planned_task_id,
            "is_work": self.is_work,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class LedgerEntry(Base):
    """个人流水记账：仅做录入。"""
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    amount = Column(Float, nullable=False)
    entry_type = Column(String(10), nullable=False, index=True)    # income / expense
    category = Column(String(20), default="其他", index=True)
    note = Column(Text, default="")
    entry_date = Column(DateTime, nullable=False, index=True)      # 业务记账日期（当天零点）
    raw_input = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "amount": self.amount,
            "entry_type": self.entry_type,
            "category": self.category,
            "note": self.note,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "raw_input": self.raw_input,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def init_db():
    """首次启动时创建表"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖注入用的会话生成器"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
