"""自然语言查询日程 —— LLM 把自然语言翻译成查询条件，后端拼 SQL。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import Schedule
from app.llm.claude import llm_client
from app.config import TIMEZONE

QUERY_PROMPT_TEMPLATE = """你是日程查询助手，把用户的中文自然语言查询翻译成 JSON 格式的查询条件。

当前时间：{now}
时区：{tz}

输出 JSON 字段（不需要的字段可不输出或设为 null）：
- status (string|null): 状态筛选，从 ["pending", "done", "cancelled", "overdue"] 选；不限制就 null
  - overdue 表示"已经过了开始时间但状态仍是 pending"的逾期日程
- time_from (string|null): ISO 8601 起始时间，可为 null
- time_to (string|null): ISO 8601 结束时间，可为 null
- category (string|null): 类别筛选，从 ["会议","工作","生活","学习","运动","其他"] 选
- priority (string|null): 优先级筛选，从 ["高","中","低"] 选
- keyword (string|null): 标题/描述模糊关键词

常见映射：
- "未完成""还没做""待办" → status=pending
- "已完成""做完的" → status=done
- "逾期""过期""超时" → status=overdue
- "今天" → time_from=今天 00:00, time_to=今天 23:59
- "本周" → 本周一 00:00 到 本周日 23:59
- "下周" → 下周一 00:00 到 下周日 23:59
- "本月" → 本月 1 号 00:00 到 月末 23:59

严格规则：
- 只输出 JSON，不要 markdown、不要其他文字
- 没有信息的字段用 null

用户查询：{user_input}
"""


def parse_query(user_input: str) -> dict:
    """LLM 解析查询条件"""
    now = datetime.now(ZoneInfo(TIMEZONE))
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    now_str = now.strftime(f"%Y-%m-%d %H:%M ({weekday_zh})")

    system_prompt = QUERY_PROMPT_TEMPLATE.format(
        now=now_str, tz=TIMEZONE, user_input="{user_input}"
    )
    return llm_client.chat_json(system_prompt=system_prompt, user_input=user_input)


def execute_query(db: Session, conditions: dict) -> list[dict]:
    """根据 LLM 解析出的条件查询数据库"""
    q = db.query(Schedule)

    status = conditions.get("status")
    if status == "overdue":
        now = datetime.now()
        q = q.filter(and_(Schedule.status == "pending", Schedule.start_time < now))
    elif status:
        q = q.filter(Schedule.status == status)

    if conditions.get("time_from"):
        q = q.filter(Schedule.start_time >= datetime.fromisoformat(conditions["time_from"]))
    if conditions.get("time_to"):
        q = q.filter(Schedule.start_time <= datetime.fromisoformat(conditions["time_to"]))
    if conditions.get("category"):
        q = q.filter(Schedule.category == conditions["category"])
    if conditions.get("priority"):
        q = q.filter(Schedule.priority == conditions["priority"])
    if conditions.get("keyword"):
        kw = f"%{conditions['keyword']}%"
        q = q.filter((Schedule.title.like(kw)) | (Schedule.description.like(kw)))

    q = q.order_by(Schedule.start_time.asc())
    return [s.to_dict() for s in q.all()]


def query_schedules(db: Session, user_input: str) -> dict:
    """完整流程：自然语言 → 条件 → 查询 → 结果"""
    conditions = parse_query(user_input)
    results = execute_query(db, conditions)
    return {
        "conditions": conditions,
        "count": len(results),
        "schedules": results,
    }
