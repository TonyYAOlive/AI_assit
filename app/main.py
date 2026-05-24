"""FastAPI 应用入口。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models import init_db
from app.api.routes import router

app = FastAPI(title="个人日程 AI 助手", version="0.1.0")

# 微信小程序请求需要允许跨域（开发时方便起见全开）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 启动时建表（首次运行自动创建 schedule.db）
init_db()

app.include_router(router, prefix="/api")


@app.get("/")
def root():
    return {"name": "schedule-ai", "status": "ok"}
