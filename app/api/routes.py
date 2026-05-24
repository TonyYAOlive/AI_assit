"""HTTP 路由。"""
import json
import urllib.request
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
from app.models import Schedule, get_db
from app.schemas import TextInput, StatusUpdate
from app.services.parser import parse_schedule
from app.services.query import query_schedules

router = APIRouter()


def _send_telegram_message(chat_id: int, text: str) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("请在 .env 中设置 TELEGRAM_BOT_TOKEN")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


@router.post("/telegram/webhook/{token}")
def telegram_webhook(token: str, update: dict, db: Session = Depends(get_db)):
    """Telegram webhook，用于接收机器人消息并回复。"""
    if not TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(500, "Telegram webhook secret 未配置")
    if token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(403, "token mismatch")

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}

    if text.startswith("/start"):
        reply = (
            "欢迎使用日程助手。\n"
            "直接发送一句话创建日程，或者发送 /query 后跟查询内容。"
        )
        _send_telegram_message(chat_id, reply)
        return {"ok": True}

    if text.startswith("/query"):
        query_text = text[len("/query"):].strip() or "今天还有什么未完成的？"
        result = query_schedules(db, query_text)
        if not result["schedules"]:
            reply = "没有符合条件的日程。"
        else:
            lines = []
            for item in result["schedules"][:10]:
                lines.append(
                    f"{item['title']} | {item['start_time'][:16]} | {item['status']} | {item['priority']}"
                )
            reply = f"共找到 {result['count']} 条日程：\n" + "\n".join(lines)
        _send_telegram_message(chat_id, reply)
        return {"ok": True}

    parsed = parse_schedule(text)
    schedule = Schedule(
        title=parsed["title"],
        description=parsed.get("description", ""),
        start_time=datetime.fromisoformat(parsed["start_time"]),
        end_time=datetime.fromisoformat(parsed["end_time"]) if parsed.get("end_time") else None,
        duration_minutes=parsed.get("duration_minutes", 60),
        location=parsed.get("location", ""),
        participants=parsed.get("participants", []),
        category=parsed.get("category", "其他"),
        priority=parsed.get("priority", "中"),
        reminder_minutes_before=parsed.get("reminder_minutes_before", 15),
        raw_input=text,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    reply = (
        "已创建日程：\n"
        f"{schedule.title}\n"
        f"开始：{schedule.start_time.isoformat()}\n"
        f"时长：{schedule.duration_minutes} 分钟\n"
        f"优先级：{schedule.priority}"
    )
    _send_telegram_message(chat_id, reply)
    return {"ok": True}


@router.post("/parse")
def parse_and_save(payload: TextInput, db: Session = Depends(get_db)):
    """一句话 → 解析 → 入库，返回完整日程"""
    if not payload.text.strip():
        raise HTTPException(400, "输入不能为空")

    try:
        parsed = parse_schedule(payload.text)
    except Exception as e:
        raise HTTPException(500, f"解析失败：{e}")

    schedule = Schedule(
        title=parsed["title"],
        description=parsed.get("description", ""),
        start_time=datetime.fromisoformat(parsed["start_time"]),
        end_time=datetime.fromisoformat(parsed["end_time"]) if parsed.get("end_time") else None,
        duration_minutes=parsed.get("duration_minutes", 60),
        location=parsed.get("location", ""),
        participants=parsed.get("participants", []),
        category=parsed.get("category", "其他"),
        priority=parsed.get("priority", "中"),
        reminder_minutes_before=parsed.get("reminder_minutes_before", 15),
        raw_input=payload.text,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule.to_dict()


@router.post("/query")
def query(payload: TextInput, db: Session = Depends(get_db)):
    """自然语言查询日程"""
    if not payload.text.strip():
        raise HTTPException(400, "输入不能为空")
    try:
        return query_schedules(db, payload.text)
    except Exception as e:
        raise HTTPException(500, f"查询失败：{e}")


@router.get("/schedules")
def list_all(status: str | None = None, db: Session = Depends(get_db)):
    """直接列出所有日程，可按 status 过滤（不依赖 LLM，用于小程序首页列表）"""
    q = db.query(Schedule)
    if status:
        q = q.filter(Schedule.status == status)
    q = q.order_by(Schedule.start_time.asc())
    return [s.to_dict() for s in q.all()]


@router.patch("/schedules/{schedule_id}/status")
def update_status(schedule_id: int, payload: StatusUpdate, db: Session = Depends(get_db)):
    """更新状态：标记完成、取消等"""
    if payload.status not in ("pending", "done", "cancelled"):
        raise HTTPException(400, "status 只能是 pending/done/cancelled")
    s = db.query(Schedule).get(schedule_id)
    if not s:
        raise HTTPException(404, "日程不存在")
    s.status = payload.status
    db.commit()
    db.refresh(s)
    return s.to_dict()


@router.delete("/schedules/{schedule_id}")
def delete(schedule_id: int, db: Session = Depends(get_db)):
    s = db.query(Schedule).get(schedule_id)
    if not s:
        raise HTTPException(404, "日程不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}
