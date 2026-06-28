"""请求和响应的 Pydantic 模型。"""
from pydantic import BaseModel


class TextInput(BaseModel):
    text: str


class StatusUpdate(BaseModel):
    status: str  # pending / done / cancelled


class MemoCreate(BaseModel):
    content: str
    memo_type: str = "temporary"       # temporary / long_term
    priority: str = "low"              # low / normal / high / urgent
    due_time: str | None = None        # ISO 8601，可为空
    planned_time: str | None = None    # ISO 8601，可为空
    estimated_minutes: int = 30


class MemoStatusUpdate(BaseModel):
    status: str  # pending / planned / done / cancelled / expired


class TaskStatusUpdate(BaseModel):
    status: str  # pending / done / cancelled


class DayPlanStatusUpdate(BaseModel):
    status: str  # pending / done / cancelled
