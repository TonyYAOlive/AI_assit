"""计划生成器：周计划任务列表 + 当日具体时间计划。"""
import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import TIMEZONE
from app.llm.claude import llm_client
from app.models import Memo, PlannedTask, DayPlan

WEEKLY_PLAN_PROMPT_TEMPLATE = """你是个人助手，根据用户的备忘录和长期任务，为他生成下周的计划任务列表。

当前时间：{now}
下周范围：{week_start} 到 {week_end}（周一到周日）

用户的待办备忘录：
{memos_text}

用户的长期任务：
{tasks_text}

请为下周生成合理的计划任务列表。要求：
- 每项任务只有时长（duration_hrs），不需要具体时间
- 根据备忘截止时间和优先级合理安排
- 长期任务按频率分解（如每周5小时→分5次，每次1小时）
- source_type 填 "memo"（来自备忘录）或 "task"（来自长期任务），source_id 填对应 ID
- 手动添加的额外项 source_type 填 "manual"，source_id 填 null

输出 JSON 数组，每项字段：
- title (string)：任务标题
- description (string)：描述，可为空字符串
- duration_hrs (float)：预计时长（小时），精度 0.5
- category (string)：从 ["会议","工作","生活","学习","运动","其他"] 选一个
- priority (string)：高/中/低
- source_type (string)：memo/task/manual
- source_id (int|null)：来源 ID
- notes (string)：备注，可为空字符串

严格规则：
- 只输出 JSON 数组，不要任何额外文字或 markdown 包裹
- 如果没有任何待办事项，输出空数组 []
"""

DAY_PLAN_PROMPT_TEMPLATE = """你是个人助手，根据用户今天的待完成任务，生成今日具体时间计划。

当前时间：{now}
今天日期：{today}（{weekday}）
{work_note}

本周待完成任务：
{tasks_text}

请把这些任务安排到今天的可用时段中。要求：
- 输出每项任务的具体开始和结束时间
- 避免时间冲突
- 合理安排任务顺序（高优先级靠前，运动类适合早晨或傍晚）
- 不要安排超过 23:00 的任务
- planned_task_id 填对应任务的 ID（整数）

输出 JSON 数组，每项字段：
- title (string)：任务标题
- start_time (string)：ISO 8601 格式，例如 "2026-06-29T09:00:00"
- end_time (string)：ISO 8601 格式
- category (string)：从 ["会议","工作","生活","学习","运动","其他"] 选一个
- priority (string)：高/中/低
- planned_task_id (int|null)：来源 PlannedTask 的 ID
- notes (string)：备注，可为空字符串

严格规则：
- 只输出 JSON 数组，不要任何额外文字或 markdown 包裹
- 如果没有任务可安排，输出空数组 []
"""


def _week_start(dt: datetime) -> datetime:
    """返回给定时间所在周或下周的周一零点。"""
    d = dt.date()
    days_ahead = (7 - d.weekday()) % 7  # 到下周一的天数
    if days_ahead == 0:
        days_ahead = 7
    monday = d + timedelta(days=days_ahead)
    return datetime(monday.year, monday.month, monday.day)


def generate_weekly_tasks(db: Session, now: datetime = None) -> list[dict]:
    """根据备忘录和长期任务，LLM 生成下周 PlannedTask 列表并写入数据库。
    now 参数仅供测试注入，生产调用不传。
    """
    if now is None:
        now = datetime.now(ZoneInfo(TIMEZONE))
    week_start = _week_start(now)
    week_end = week_start + timedelta(days=6)

    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    now_str = now.strftime(f"%Y-%m-%d %H:%M ({weekday_zh})")

    memos = db.query(Memo).filter(
        Memo.memo_type == "temporary",
        Memo.status == "pending",
    ).all()

    tasks = db.query(Memo).filter(
        Memo.memo_type == "long_term",
        Memo.status == "pending",
    ).all()

    def _fmt_memo(m: Memo) -> str:
        due = f"，截止 {m.due_time.strftime('%Y-%m-%d')}" if m.due_time else ""
        return f"  [ID:{m.id}] {m.content}（优先级:{m.priority}{due}）"

    memos_text = "\n".join(_fmt_memo(m) for m in memos) or "  （无待办备忘录）"
    tasks_text = "\n".join(
        f"  [ID:{t.id}] {t.content}（优先级:{t.priority}，预计每次 {t.estimated_minutes} 分钟）"
        for t in tasks
    ) or "  （无长期任务）"

    system_prompt = WEEKLY_PLAN_PROMPT_TEMPLATE.format(
        now=now_str,
        week_start=week_start.strftime("%Y-%m-%d"),
        week_end=week_end.strftime("%Y-%m-%d"),
        memos_text=memos_text,
        tasks_text=tasks_text,
    )

    items = llm_client.chat_json(system_prompt=system_prompt, user_input="请生成下周计划", max_tokens=4096)
    if not isinstance(items, list):
        items = []

    created = []
    for item in items:
        task = PlannedTask(
            title=item.get("title", "未命名任务"),
            description=item.get("description", ""),
            week_start_date=week_start,
            duration_hrs=float(item.get("duration_hrs", 1.0)),
            category=item.get("category", "其他"),
            priority=item.get("priority", "中"),
            source_type=item.get("source_type", "manual"),
            source_id=item.get("source_id"),
            notes=item.get("notes", ""),
        )
        db.add(task)
        db.flush()
        created.append(task.to_dict())

    db.commit()
    return created


def generate_day_plan(db: Session, now: datetime = None) -> list[dict]:
    """根据本周 PlannedTask，LLM 生成今日 DayPlan 并写入数据库。
    now 参数仅供测试注入，生产调用不传。
    """
    if now is None:
        now = datetime.now(ZoneInfo(TIMEZONE))
    today = datetime(now.year, now.month, now.day)
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    now_str = now.strftime(f"%Y-%m-%d %H:%M ({weekday_zh})")
    is_weekday = now.weekday() < 5

    # 本周周一
    this_monday = today - timedelta(days=now.weekday())

    pending_tasks = db.query(PlannedTask).filter(
        PlannedTask.week_start_date == this_monday,
        PlannedTask.status == "pending",
    ).all()

    created = []

    # 工作日先插入工作占位条目
    if is_weekday:
        work_entry = DayPlan(
            title="工作",
            plan_date=today,
            start_time=today.replace(hour=7, minute=0),
            end_time=today.replace(hour=15, minute=0),
            duration_minutes=480,
            category="工作",
            priority="高",
            planned_task_id=None,
            is_work=True,
            notes="工作时间占位",
        )
        db.add(work_entry)
        db.flush()
        created.append(work_entry.to_dict())

    if not pending_tasks:
        db.commit()
        return created

    tasks_text = "\n".join(
        f"  [ID:{t.id}] {t.title}，时长 {t.duration_hrs} 小时，优先级 {t.priority}，类别 {t.category}"
        for t in pending_tasks
    )

    if is_weekday:
        work_note = "今天是工作日，工作时间为 07:00~15:00（已占用），请在 15:00 之后或 07:00 之前安排其他任务。"
    else:
        work_note = "今天是周末，全天均可安排。"

    system_prompt = DAY_PLAN_PROMPT_TEMPLATE.format(
        now=now_str,
        today=today.strftime("%Y-%m-%d"),
        weekday=weekday_zh,
        work_note=work_note,
        tasks_text=tasks_text,
    )

    items = llm_client.chat_json(system_prompt=system_prompt, user_input="请生成今日计划", max_tokens=4096)
    if not isinstance(items, list):
        items = []

    for item in items:
        try:
            start_dt = datetime.fromisoformat(item["start_time"])
            end_dt = datetime.fromisoformat(item["end_time"])
        except (KeyError, ValueError):
            continue

        duration_min = int((end_dt - start_dt).total_seconds() / 60)
        entry = DayPlan(
            title=item.get("title", "未命名"),
            plan_date=today,
            start_time=start_dt,
            end_time=end_dt,
            duration_minutes=duration_min,
            category=item.get("category", "其他"),
            priority=item.get("priority", "中"),
            planned_task_id=item.get("planned_task_id"),
            is_work=False,
            notes=item.get("notes", ""),
        )
        db.add(entry)
        db.flush()
        created.append(entry.to_dict())

    db.commit()
    return created
