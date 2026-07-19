"""HTTP 路由。"""
import json
import urllib.request
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
from app.models import Schedule, Memo, PlannedTask, DayPlan, get_db
from app.schemas import TextInput, StatusUpdate, MemoCreate, MemoStatusUpdate, TaskStatusUpdate, DayPlanStatusUpdate
from app.services.parser import parse_schedule
from app.services.query import query_schedules
from app.services.router import route as llm_route
from app.services.planner import (
    generate_weekly_tasks,
    generate_day_plan,
    format_weekly_plan,
    get_or_generate_weekly_plan,
    get_or_generate_day_plan,
    format_day_plan,
)
from app.tools.memo_tool import memo_create

router = APIRouter()

# ── Telegram 发消息 ───────────────────────────────────────────────────────────

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


# ── Telegram Webhook ──────────────────────────────────────────────────────────

_START_HELP = (
    "欢迎使用日程助手！\n\n"
    "命令列表：\n"
    "/memo <内容>   — 添加备忘录（可加截止时间）\n"
    "/task <内容>   — 添加长期任务\n"
    "/memos        — 查看待办备忘录\n"
    "/tasks        — 查看长期任务\n"
    "/week_plan    — 查看/生成周计划（周日20点后为下周）\n"
    "/day_plan     — 查看/生成今日计划\n\n"
    "也可以直接说话，我会判断你的意图。"
)


def _reply_week_plan(db: Session) -> str:
    """查询/生成周计划，返回格式化好的回复文本。"""
    try:
        tasks, week_start, generated = get_or_generate_weekly_plan(db)
    except Exception as e:
        return f"获取周计划失败，请稍后重试。（{e}）"
    if not tasks:
        return "暂无备忘录或长期任务，无法生成计划。"
    return format_weekly_plan(tasks, week_start)


def _reply_day_plan(db: Session) -> str:
    """查询/生成今日计划，返回格式化好的回复文本。"""
    try:
        entries, generated = get_or_generate_day_plan(db)
    except Exception as e:
        return f"获取今日计划失败，请稍后重试。（{e}）"
    if not entries:
        return "今日暂无计划任务。"
    return format_day_plan(entries)


def _handle_command(text: str, db: Session, chat_id: int):
    """处理命令前缀消息，返回回复文本。"""
    if text.startswith("/start"):
        return _START_HELP

    if text.startswith("/memo"):
        content = text[len("/memo"):].strip()
        if not content:
            return "请在 /memo 后面写备忘内容，例如：/memo 周五前交报告"
        result = memo_create(db=db, content=content, memo_type="temporary", raw_input=text)
        return f"已添加备忘录：{result['content']}"

    if text.startswith("/task"):
        content = text[len("/task"):].strip()
        if not content:
            return "请在 /task 后面写任务内容，例如：/task 每周英语学习5小时"
        result = memo_create(db=db, content=content, memo_type="long_term", raw_input=text)
        return f"已添加长期任务：{result['content']}"

    if text.startswith("/week_plan"):
        return _reply_week_plan(db)

    if text.startswith("/day_plan"):
        return _reply_day_plan(db)

    if text.startswith("/memos"):
        memos = db.query(Memo).filter(
            Memo.memo_type == "temporary",
            Memo.status == "pending",
        ).order_by(Memo.created_at.desc()).all()
        if not memos:
            return "没有待办备忘录。"
        lines = [f"待办备忘录（共{len(memos)}条）："]
        for m in memos[:15]:
            due = f" | 截止 {m.due_time.strftime('%m-%d')}" if m.due_time else ""
            lines.append(f"• [{m.id}] {m.content}{due}")
        return "\n".join(lines)

    if text.startswith("/tasks"):
        tasks = db.query(Memo).filter(
            Memo.memo_type == "long_term",
            Memo.status == "pending",
        ).order_by(Memo.created_at.desc()).all()
        if not tasks:
            return "没有长期任务。"
        lines = [f"长期任务（共{len(tasks)}条）："]
        for t in tasks[:15]:
            lines.append(f"• [{t.id}] {t.content}")
        return "\n".join(lines)

    if text.startswith("/plan"):
        return _reply_week_plan(db)

    if text.startswith("/today"):
        return _reply_day_plan(db)

    return None  # 不是已知命令


def _handle_nlp(text: str, db: Session) -> str:
    """纯自然语言：调用 LLM 路由器分发。"""
    try:
        result = llm_route(text)
    except Exception as e:
        return f"解析失败，请换种说法试试。（{e}）"

    intent = result.get("intent", "unknown")
    data = result.get("data", {})

    if intent == "add_memo":
        content = data.get("content", text)
        priority = data.get("priority", "low")
        due_time_str = data.get("due_time")
        due_time = datetime.fromisoformat(due_time_str) if due_time_str else None
        m = memo_create(db=db, content=content, memo_type="temporary",
                        priority=priority, due_time=due_time, raw_input=text)
        return f"已添加备忘录：{m['content']}"

    if intent == "add_long_term_task":
        content = data.get("content", text)
        m = memo_create(db=db, content=content, memo_type="long_term", raw_input=text)
        return f"已添加长期任务：{m['content']}"

    if intent in ("generate_weekly_plan", "query_plan"):
        return _reply_week_plan(db)

    if intent in ("generate_day_plan", "query_today"):
        return _reply_day_plan(db)

    if intent == "query_memo":
        memos = db.query(Memo).filter(
            Memo.memo_type == "temporary",
            Memo.status == "pending",
        ).order_by(Memo.created_at.desc()).limit(10).all()
        if not memos:
            return "没有待办备忘录。"
        lines = [f"待办备忘录（{len(memos)}条）："]
        for m in memos:
            lines.append(f"• {m.content}")
        return "\n".join(lines)

    if intent == "query_task":
        tasks = db.query(Memo).filter(
            Memo.memo_type == "long_term",
            Memo.status == "pending",
        ).order_by(Memo.created_at.desc()).limit(10).all()
        if not tasks:
            return "没有长期任务。"
        lines = [f"长期任务（{len(tasks)}条）："]
        for t in tasks:
            lines.append(f"• {t.content}")
        return "\n".join(lines)

    if intent == "greet":
        return "你好！有什么可以帮你？发送 /start 查看所有命令。"

    return "没太明白你的意思，发送 /start 查看支持的命令。"


@router.post("/telegram/webhook/{token}")
def telegram_webhook(token: str, update: dict, db: Session = Depends(get_db)):
    """Telegram webhook，支持命令前缀直接分发和自然语言 LLM 路由。"""
    if not TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(500, "Telegram webhook secret 未配置")
    if token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(403, "token mismatch")

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}

    # 命令前缀：直接处理，不走 LLM
    if text.startswith("/"):
        reply = _handle_command(text, db, chat_id)
        if reply is None:
            reply = "未知命令，发送 /start 查看所有命令。"
    else:
        reply = _handle_nlp(text, db)

    _send_telegram_message(chat_id, reply)
    return {"ok": True}


# ── Schedule 日程（原有功能保留）────────────────────────────────────────────────

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
    """直接列出所有日程，可按 status 过滤"""
    q = db.query(Schedule)
    if status:
        q = q.filter(Schedule.status == status)
    q = q.order_by(Schedule.start_time.asc())
    return [s.to_dict() for s in q.all()]


@router.patch("/schedules/{schedule_id}/status")
def update_status(schedule_id: int, payload: StatusUpdate, db: Session = Depends(get_db)):
    """更新日程状态"""
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


# ── Memo 备忘录 CRUD ──────────────────────────────────────────────────────────

_VALID_MEMO_STATUSES = {"pending", "planned", "done", "cancelled", "expired"}
_VALID_MEMO_TYPES = {"temporary", "long_term"}
_VALID_MEMO_PRIORITIES = {"low", "normal", "high", "urgent"}


@router.post("/memos")
def create_memo(payload: MemoCreate, db: Session = Depends(get_db)):
    """创建备忘录"""
    if not payload.content.strip():
        raise HTTPException(400, "content 不能为空")
    if payload.memo_type not in _VALID_MEMO_TYPES:
        raise HTTPException(400, f"memo_type 只能是 {sorted(_VALID_MEMO_TYPES)}")
    if payload.priority not in _VALID_MEMO_PRIORITIES:
        raise HTTPException(400, f"priority 只能是 {sorted(_VALID_MEMO_PRIORITIES)}")

    due_time = datetime.fromisoformat(payload.due_time) if payload.due_time else None
    planned_time = datetime.fromisoformat(payload.planned_time) if payload.planned_time else None

    return memo_create(
        db=db,
        content=payload.content.strip(),
        memo_type=payload.memo_type,
        priority=payload.priority,
        due_time=due_time,
        planned_time=planned_time,
        estimated_minutes=payload.estimated_minutes,
    )


@router.get("/memos")
def list_memos(
    status: str | None = None,
    memo_type: str | None = None,
    priority: str | None = None,
    db: Session = Depends(get_db),
):
    """列出备忘录，支持按 status / memo_type / priority 过滤"""
    q = db.query(Memo)
    if status:
        q = q.filter(Memo.status == status)
    if memo_type:
        q = q.filter(Memo.memo_type == memo_type)
    if priority:
        q = q.filter(Memo.priority == priority)
    q = q.order_by(Memo.created_at.desc())
    return [m.to_dict() for m in q.all()]


@router.get("/memos/{memo_id}")
def get_memo(memo_id: int, db: Session = Depends(get_db)):
    """获取单条备忘录"""
    m = db.get(Memo, memo_id)
    if not m:
        raise HTTPException(404, "备忘录不存在")
    return m.to_dict()


@router.patch("/memos/{memo_id}/status")
def update_memo_status(memo_id: int, payload: MemoStatusUpdate, db: Session = Depends(get_db)):
    """更新备忘录状态"""
    if payload.status not in _VALID_MEMO_STATUSES:
        raise HTTPException(400, f"status 只能是 {sorted(_VALID_MEMO_STATUSES)}")
    m = db.get(Memo, memo_id)
    if not m:
        raise HTTPException(404, "备忘录不存在")
    m.status = payload.status
    db.commit()
    db.refresh(m)
    return m.to_dict()


@router.delete("/memos/{memo_id}")
def delete_memo(memo_id: int, db: Session = Depends(get_db)):
    """删除备忘录"""
    m = db.get(Memo, memo_id)
    if not m:
        raise HTTPException(404, "备忘录不存在")
    db.delete(m)
    db.commit()
    return {"ok": True}


# ── PlannedTask 周计划任务 CRUD ───────────────────────────────────────────────

_VALID_TASK_STATUSES = {"pending", "done", "cancelled"}


@router.post("/plans/generate")
def api_generate_weekly_tasks(db: Session = Depends(get_db)):
    """强制重新生成目标周计划（允许重复，Telegram 端请使用 /week_plan）"""
    try:
        tasks = generate_weekly_tasks(db)
    except Exception as e:
        raise HTTPException(500, f"生成失败：{e}")
    return {"count": len(tasks), "tasks": tasks}


@router.get("/plans")
def list_plans(
    week_start: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """列出 PlannedTask，可按 week_start（YYYY-MM-DD）和 status 过滤"""
    q = db.query(PlannedTask)
    if week_start:
        try:
            ws = datetime.fromisoformat(week_start)
        except ValueError:
            raise HTTPException(400, "week_start 格式应为 YYYY-MM-DD")
        q = q.filter(PlannedTask.week_start_date == ws)
    if status:
        q = q.filter(PlannedTask.status == status)
    q = q.order_by(PlannedTask.week_start_date.desc(), PlannedTask.priority.asc())
    return [t.to_dict() for t in q.all()]


@router.get("/plans/{task_id}")
def get_plan(task_id: int, db: Session = Depends(get_db)):
    """获取单条 PlannedTask"""
    t = db.get(PlannedTask, task_id)
    if not t:
        raise HTTPException(404, "计划任务不存在")
    return t.to_dict()


@router.patch("/plans/{task_id}/status")
def update_plan_status(task_id: int, payload: TaskStatusUpdate, db: Session = Depends(get_db)):
    """更新 PlannedTask 状态"""
    if payload.status not in _VALID_TASK_STATUSES:
        raise HTTPException(400, f"status 只能是 {sorted(_VALID_TASK_STATUSES)}")
    t = db.get(PlannedTask, task_id)
    if not t:
        raise HTTPException(404, "计划任务不存在")
    t.status = payload.status
    db.commit()
    db.refresh(t)
    return t.to_dict()


@router.delete("/plans/{task_id}")
def delete_plan(task_id: int, db: Session = Depends(get_db)):
    """删除 PlannedTask"""
    t = db.get(PlannedTask, task_id)
    if not t:
        raise HTTPException(404, "计划任务不存在")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ── DayPlan 当日计划 CRUD ─────────────────────────────────────────────────────

_VALID_DAY_PLAN_STATUSES = {"pending", "done", "cancelled"}


@router.post("/day_plans/generate")
def api_generate_day_plan(db: Session = Depends(get_db)):
    """触发生成今日 DayPlan"""
    try:
        entries = generate_day_plan(db)
    except Exception as e:
        raise HTTPException(500, f"生成失败：{e}")
    return {"count": len(entries), "entries": entries}


@router.get("/day_plans")
def list_day_plans(
    date: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """列出 DayPlan，可按 date（YYYY-MM-DD）和 status 过滤"""
    q = db.query(DayPlan)
    if date:
        try:
            plan_date = datetime.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date 格式应为 YYYY-MM-DD")
        q = q.filter(DayPlan.plan_date == plan_date)
    if status:
        q = q.filter(DayPlan.status == status)
    q = q.order_by(DayPlan.plan_date.desc(), DayPlan.start_time.asc())
    return [e.to_dict() for e in q.all()]


@router.get("/day_plans/{entry_id}")
def get_day_plan(entry_id: int, db: Session = Depends(get_db)):
    """获取单条 DayPlan"""
    e = db.get(DayPlan, entry_id)
    if not e:
        raise HTTPException(404, "当日计划不存在")
    return e.to_dict()


@router.patch("/day_plans/{entry_id}/status")
def update_day_plan_status(entry_id: int, payload: DayPlanStatusUpdate, db: Session = Depends(get_db)):
    """更新 DayPlan 状态"""
    if payload.status not in _VALID_DAY_PLAN_STATUSES:
        raise HTTPException(400, f"status 只能是 {sorted(_VALID_DAY_PLAN_STATUSES)}")
    e = db.get(DayPlan, entry_id)
    if not e:
        raise HTTPException(404, "当日计划不存在")
    e.status = payload.status
    db.commit()
    db.refresh(e)
    return e.to_dict()


@router.delete("/day_plans/{entry_id}")
def delete_day_plan(entry_id: int, db: Session = Depends(get_db)):
    """删除 DayPlan"""
    e = db.get(DayPlan, entry_id)
    if not e:
        raise HTTPException(404, "当日计划不存在")
    db.delete(e)
    db.commit()
    return {"ok": True}
