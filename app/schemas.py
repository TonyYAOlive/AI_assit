"""请求和响应的 Pydantic 模型。"""
from pydantic import BaseModel


class TextInput(BaseModel):
    text: str


class StatusUpdate(BaseModel):
    status: str  # pending / done / cancelled
