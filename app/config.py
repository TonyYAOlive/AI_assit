"""配置加载，从环境变量读取。"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 用 config.py 所在目录推算 .env 绝对路径，避免工作目录影响
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-4-7")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./schedule.db")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai")

if not ANTHROPIC_API_KEY:
    raise RuntimeError("请在 .env 中设置 ANTHROPIC_API_KEY")
