"""数据库模型定义。"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON
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
